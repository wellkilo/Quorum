"""Transactional persistence for autonomy, routing decisions, and interrupt spend."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from quorum.db_models import (
    ActionDecisionRow,
    AutonomyProfileRow,
    InterruptBudgetAccountRow,
    InterruptEventRow,
    OrganizationRow,
)
from quorum.models import (
    ActionRequest,
    AutonomyLevel,
    AutonomySnapshot,
    DecisionStatus,
    InterruptResolution,
    ParticipantResponse,
    PolicyDecision,
    TaskClass,
)
from quorum.policy import (
    INTERRUPT_BUDGET_WINDOW,
    approval_transition,
    plan_policy_decision,
    rejection_transition,
    undo_transition,
)


class ActionDecisionConflictError(RuntimeError):
    """Raised when an action ID is reused with different request content."""


def _fingerprint(request: ActionRequest) -> str:
    return sha256(request.model_dump_json(exclude_none=False).encode("utf-8")).hexdigest()


def _interrupt_event_id(
    organization_id: str, action_id: str, participant_id: str, event_type: str
) -> str:
    material = "|".join((organization_id, action_id, participant_id, event_type))
    return f"int_{sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class DecisionPolicyStore:
    """Persist deterministic policy decisions and rolling interrupt-budget evidence."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name not in {"postgresql", "sqlite"}:
            raise ValueError("DecisionPolicyStore supports PostgreSQL and SQLite only")
        self._engine = engine
        self._session_factory = sessionmaker(engine, expire_on_commit=False)

    def close(self) -> None:
        self._engine.dispose()

    def autonomy_for(self, organization_id: str, action_class: TaskClass) -> AutonomySnapshot:
        with self._session_factory() as session:
            row = session.get(AutonomyProfileRow, (organization_id, action_class.value))
            return self._to_autonomy(row) if row is not None else AutonomySnapshot()

    def get_decision(self, organization_id: str, action_id: str) -> PolicyDecision | None:
        with self._session_factory() as session:
            row = session.get(ActionDecisionRow, (organization_id, action_id))
            return self._to_decision(row) if row is not None else None

    def interrupt_spend(
        self, organization_id: str, participant_ids: list[str], *, now: datetime | None = None
    ) -> dict[str, int]:
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        if not participant_ids:
            return {}
        with self._session_factory() as session:
            rows = session.execute(
                select(InterruptEventRow.participant_id, func.count(InterruptEventRow.event_id))
                .where(
                    InterruptEventRow.organization_id == organization_id,
                    InterruptEventRow.participant_id.in_(participant_ids),
                    InterruptEventRow.event_type == "requested",
                    InterruptEventRow.occurred_at >= evaluated_at - INTERRUPT_BUDGET_WINDOW,
                    InterruptEventRow.occurred_at <= evaluated_at,
                )
                .group_by(InterruptEventRow.participant_id)
            ).all()
        counts = {participant_id: int(count) for participant_id, count in rows}
        return {participant_id: counts.get(participant_id, 0) for participant_id in participant_ids}

    def decide(self, request: ActionRequest, *, now: datetime | None = None) -> PolicyDecision:
        evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
        with self._session_factory.begin() as session:
            self._ensure_organization(session, request.organization_id, evaluated_at)
            existing = session.get(ActionDecisionRow, (request.organization_id, request.action_id))
            request_fingerprint = _fingerprint(request)
            if existing is not None:
                if existing.request_fingerprint != request_fingerprint:
                    raise ActionDecisionConflictError(
                        "action identity was reused with different request content"
                    )
                return self._to_decision(existing)
            autonomy_row = self._lock_autonomy_profile(
                session, request.organization_id, request.action_class, evaluated_at
            )
            self._lock_budget_accounts(
                session, request.organization_id, request.candidate_decider_ids, evaluated_at
            )
            spend = self._interrupt_spend_in_session(
                session, request.organization_id, request.candidate_decider_ids, evaluated_at
            )
            decision = plan_policy_decision(
                request, self._to_autonomy(autonomy_row), spend, now=evaluated_at
            )

            session.add(
                ActionDecisionRow(
                    organization_id=request.organization_id,
                    action_id=request.action_id,
                    request_fingerprint=request_fingerprint,
                    requested_by_id=request.requested_by_id,
                    action_class=request.action_class.value,
                    tool_name=request.tool_name,
                    arguments_fingerprint=decision.arguments_fingerprint,
                    risk_score=decision.risk.score,
                    risk_tier=decision.risk.tier.value,
                    risk_payload=decision.risk.model_dump(mode="json"),
                    autonomy_payload=decision.autonomy.model_dump(mode="json"),
                    required_quorum=decision.required_quorum,
                    selected_decider_ids=decision.selected_decider_ids,
                    budget_payload=[budget.model_dump(mode="json") for budget in decision.budgets],
                    status=decision.status.value,
                    timeout_at=decision.timeout_at,
                    timeout_default=decision.timeout_default.value,
                    requested_at=request.requested_at,
                    created_at=evaluated_at,
                    updated_at=evaluated_at,
                )
            )
            for participant_id in decision.selected_decider_ids:
                session.add(
                    InterruptEventRow(
                        event_id=_interrupt_event_id(
                            request.organization_id, request.action_id, participant_id, "requested"
                        ),
                        organization_id=request.organization_id,
                        action_id=request.action_id,
                        participant_id=participant_id,
                        event_type="requested",
                        occurred_at=evaluated_at,
                    )
                )
        return decision

    def resolve(
        self,
        organization_id: str,
        resolution: InterruptResolution,
        *,
        now: datetime | None = None,
    ) -> DecisionStatus:
        resolved_at = (now or datetime.now(UTC)).astimezone(UTC)
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(ActionDecisionRow)
                .where(
                    ActionDecisionRow.organization_id == organization_id,
                    ActionDecisionRow.action_id == resolution.action_id,
                )
                .with_for_update()
            )
            if row is None:
                raise KeyError("action decision not found")
            timed_out = self._apply_timeout_if_due(session, row, resolved_at)
            if timed_out is not None:
                return timed_out
            selected = set(row.selected_decider_ids)
            if any(response.participant_id not in selected for response in resolution.responses):
                raise ValueError("response participant was not selected by the quorum router")
            existing_events = session.scalars(
                select(InterruptEventRow).where(
                    InterruptEventRow.organization_id == organization_id,
                    InterruptEventRow.action_id == resolution.action_id,
                    InterruptEventRow.event_type.in_(("approved", "rejected")),
                )
            ).all()
            by_participant = {
                item.participant_id: ParticipantResponse(
                    participant_id=item.participant_id,
                    decision="approve" if item.event_type == "approved" else "reject",
                )
                for item in existing_events
            }
            for response in resolution.responses:
                previous = by_participant.get(response.participant_id)
                if previous is not None and previous.decision != response.decision:
                    raise ValueError("a participant response cannot be changed")
            if row.status != DecisionStatus.AWAITING_APPROVAL.value:
                return DecisionStatus(row.status)

            new_responses: list[ParticipantResponse] = []
            for response in resolution.responses:
                previous = by_participant.get(response.participant_id)
                if previous is None:
                    by_participant[response.participant_id] = response
                    new_responses.append(response)
            response_status = (
                DecisionStatus.REJECTED
                if any(response.decision == "reject" for response in by_participant.values())
                else DecisionStatus.APPROVED
                if len(by_participant) >= row.required_quorum
                else DecisionStatus.AWAITING_APPROVAL
            )
            for response in new_responses:
                self._append_interrupt_response(session, row, response, resolved_at)
            if response_status is not DecisionStatus.AWAITING_APPROVAL:
                row.status = response_status.value
                row.updated_at = resolved_at
                self._transition_autonomy(
                    session,
                    row.organization_id,
                    TaskClass(row.action_class),
                    response_status,
                    resolved_at,
                )
            return response_status

    def resolve_timeout(
        self, organization_id: str, action_id: str, *, now: datetime | None = None
    ) -> DecisionStatus:
        resolved_at = (now or datetime.now(UTC)).astimezone(UTC)
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(ActionDecisionRow)
                .where(
                    ActionDecisionRow.organization_id == organization_id,
                    ActionDecisionRow.action_id == action_id,
                )
                .with_for_update()
            )
            if row is None:
                raise KeyError("action decision not found")
            if row.status not in {
                DecisionStatus.AWAITING_APPROVAL.value,
                DecisionStatus.DEFERRED_BUDGET.value,
            }:
                return DecisionStatus(row.status)
            return self._apply_timeout_if_due(session, row, resolved_at) or DecisionStatus(
                row.status
            )

    def record_undo(
        self, organization_id: str, action_id: str, *, now: datetime | None = None
    ) -> None:
        updated_at = (now or datetime.now(UTC)).astimezone(UTC)
        with self._session_factory.begin() as session:
            row = session.scalar(
                select(ActionDecisionRow)
                .where(
                    ActionDecisionRow.organization_id == organization_id,
                    ActionDecisionRow.action_id == action_id,
                )
                .with_for_update()
            )
            if row is None:
                raise KeyError("action decision not found")
            if row.status == DecisionStatus.UNDONE.value:
                return
            if row.status not in {
                DecisionStatus.AUTHORIZED.value,
                DecisionStatus.APPROVED.value,
                DecisionStatus.EXECUTED.value,
            }:
                raise ValueError("only an authorized or executed action can be undone")
            if int(row.risk_payload.get("reversibility_points", -1)) == 3:
                raise ValueError("irreversible actions cannot be undone")
            row.status = DecisionStatus.UNDONE.value
            row.updated_at = updated_at
            self._transition_autonomy(
                session,
                row.organization_id,
                TaskClass(row.action_class),
                DecisionStatus.UNDONE,
                updated_at,
            )

    def _ensure_organization(
        self, session: Session, organization_id: str, created_at: datetime
    ) -> None:
        statement = (
            postgresql_insert(OrganizationRow)
            if self._engine.dialect.name == "postgresql"
            else sqlite_insert(OrganizationRow)
        )
        session.execute(
            statement.values(
                organization_id=organization_id, created_at=created_at
            ).on_conflict_do_nothing()
        )

    def _ensure_budget_account(
        self, session: Session, organization_id: str, participant_id: str, updated_at: datetime
    ) -> None:
        statement = (
            postgresql_insert(InterruptBudgetAccountRow)
            if self._engine.dialect.name == "postgresql"
            else sqlite_insert(InterruptBudgetAccountRow)
        )
        session.execute(
            statement.values(
                organization_id=organization_id,
                participant_id=participant_id,
                updated_at=updated_at,
            ).on_conflict_do_nothing()
        )

    def _lock_budget_accounts(
        self,
        session: Session,
        organization_id: str,
        participant_ids: list[str],
        updated_at: datetime,
    ) -> None:
        for participant_id in participant_ids:
            self._ensure_budget_account(session, organization_id, participant_id, updated_at)
        session.flush()
        session.scalars(
            select(InterruptBudgetAccountRow)
            .where(
                InterruptBudgetAccountRow.organization_id == organization_id,
                InterruptBudgetAccountRow.participant_id.in_(participant_ids),
            )
            .order_by(InterruptBudgetAccountRow.participant_id)
            .with_for_update()
        ).all()

    @staticmethod
    def _interrupt_spend_in_session(
        session: Session,
        organization_id: str,
        participant_ids: list[str],
        evaluated_at: datetime,
    ) -> dict[str, int]:
        rows = session.execute(
            select(InterruptEventRow.participant_id, func.count(InterruptEventRow.event_id))
            .where(
                InterruptEventRow.organization_id == organization_id,
                InterruptEventRow.participant_id.in_(participant_ids),
                InterruptEventRow.event_type == "requested",
                InterruptEventRow.occurred_at >= evaluated_at - INTERRUPT_BUDGET_WINDOW,
                InterruptEventRow.occurred_at <= evaluated_at,
            )
            .group_by(InterruptEventRow.participant_id)
        ).all()
        counts = {participant_id: int(count) for participant_id, count in rows}
        return {participant_id: counts.get(participant_id, 0) for participant_id in participant_ids}

    def _lock_autonomy_profile(
        self,
        session: Session,
        organization_id: str,
        action_class: TaskClass,
        updated_at: datetime,
    ) -> AutonomyProfileRow:
        values = {
            "organization_id": organization_id,
            "action_class": action_class.value,
            "level": int(AutonomyLevel.ASK_FIRST),
            "consecutive_approvals": 0,
            "rejection_count": 0,
            "undo_count": 0,
            "updated_at": updated_at,
        }
        statement = (
            postgresql_insert(AutonomyProfileRow)
            if self._engine.dialect.name == "postgresql"
            else sqlite_insert(AutonomyProfileRow)
        )
        session.execute(statement.values(**values).on_conflict_do_nothing())
        row = session.scalar(
            select(AutonomyProfileRow)
            .where(
                AutonomyProfileRow.organization_id == organization_id,
                AutonomyProfileRow.action_class == action_class.value,
            )
            .with_for_update()
        )
        if row is None:
            raise RuntimeError("failed to initialize autonomy profile")
        return row

    @staticmethod
    def _append_interrupt_response(
        session: Session,
        row: ActionDecisionRow,
        response: ParticipantResponse,
        occurred_at: datetime,
    ) -> None:
        event_type = "approved" if response.decision == "approve" else "rejected"
        session.add(
            InterruptEventRow(
                event_id=_interrupt_event_id(
                    row.organization_id, row.action_id, response.participant_id, event_type
                ),
                organization_id=row.organization_id,
                action_id=row.action_id,
                participant_id=response.participant_id,
                event_type=event_type,
                occurred_at=occurred_at,
            )
        )

    def _transition_autonomy(
        self,
        session: Session,
        organization_id: str,
        action_class: TaskClass,
        status: DecisionStatus,
        updated_at: datetime,
    ) -> None:
        row = session.scalar(
            select(AutonomyProfileRow)
            .where(
                AutonomyProfileRow.organization_id == organization_id,
                AutonomyProfileRow.action_class == action_class.value,
            )
            .with_for_update()
        )
        current = self._to_autonomy(row) if row is not None else AutonomySnapshot()
        if status is DecisionStatus.APPROVED:
            transitioned = approval_transition(current)
        elif status is DecisionStatus.REJECTED:
            transitioned = rejection_transition(current)
        else:
            transitioned = undo_transition(current)
        target = row or AutonomyProfileRow(
            organization_id=organization_id, action_class=action_class.value
        )
        target.level = int(transitioned.level)
        target.consecutive_approvals = transitioned.consecutive_approvals
        target.rejection_count = transitioned.rejection_count
        target.undo_count = transitioned.undo_count
        target.updated_at = updated_at
        if row is None:
            session.add(target)

    @staticmethod
    def _apply_timeout_if_due(
        session: Session,
        row: ActionDecisionRow,
        resolved_at: datetime,
    ) -> DecisionStatus | None:
        if row.status not in {
            DecisionStatus.AWAITING_APPROVAL.value,
            DecisionStatus.DEFERRED_BUDGET.value,
        }:
            return None
        timeout_at = _normalize_datetime(row.timeout_at)
        if timeout_at is None or resolved_at < timeout_at:
            return None
        status = (
            DecisionStatus.AUTHORIZED
            if row.timeout_default == "execute_and_notify"
            else DecisionStatus.EXPIRED
        )
        if status is DecisionStatus.EXPIRED:
            responded = set(
                session.scalars(
                    select(InterruptEventRow.participant_id).where(
                        InterruptEventRow.organization_id == row.organization_id,
                        InterruptEventRow.action_id == row.action_id,
                        InterruptEventRow.event_type.in_(("approved", "rejected")),
                    )
                ).all()
            )
            for participant_id in row.selected_decider_ids:
                if participant_id in responded:
                    continue
                session.add(
                    InterruptEventRow(
                        event_id=_interrupt_event_id(
                            row.organization_id,
                            row.action_id,
                            participant_id,
                            "expired",
                        ),
                        organization_id=row.organization_id,
                        action_id=row.action_id,
                        participant_id=participant_id,
                        event_type="expired",
                        occurred_at=resolved_at,
                    )
                )
        row.status = status.value
        row.updated_at = resolved_at
        return status

    @staticmethod
    def _to_autonomy(row: AutonomyProfileRow) -> AutonomySnapshot:
        return AutonomySnapshot(
            level=AutonomyLevel(row.level),
            consecutive_approvals=row.consecutive_approvals,
            rejection_count=row.rejection_count,
            undo_count=row.undo_count,
        )

    @staticmethod
    def _to_decision(row: ActionDecisionRow) -> PolicyDecision:
        return PolicyDecision.model_validate(
            {
                "action_id": row.action_id,
                "organization_id": row.organization_id,
                "requested_by_id": row.requested_by_id,
                "action_class": row.action_class,
                "tool_name": row.tool_name,
                "arguments_fingerprint": row.arguments_fingerprint,
                "risk": row.risk_payload,
                "autonomy": row.autonomy_payload,
                "required_quorum": row.required_quorum,
                "selected_decider_ids": row.selected_decider_ids,
                "budgets": row.budget_payload,
                "status": row.status,
                "timeout_at": _normalize_datetime(row.timeout_at),
                "timeout_default": row.timeout_default,
            }
        )
