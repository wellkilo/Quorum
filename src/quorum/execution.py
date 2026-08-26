"""Idempotent execution, receipt, and single-use undo orchestration."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, urlsplit
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from quorum.db_models import (
    ActionDecisionRow,
    ActionExecutionRow,
    AutonomyProfileRow,
    ExecutionEventRow,
    UndoTokenRow,
)
from quorum.models import (
    AutonomyLevel,
    AutonomySnapshot,
    CalendarActionInput,
    DecisionStatus,
    EmailDraftActionInput,
    ExecutionProvider,
    ExecutionReceipt,
    ExecutionStatus,
    FormActionInput,
    UndoResult,
)
from quorum.policy import fingerprint_action_arguments, undo_transition
from quorum.providers import (
    CalendarProvider,
    FormProvider,
    GmailDraftProvider,
    ProviderOperationError,
    build_google_providers,
)
from quorum.slack import SlackDeliveryError, SlackNotifier, build_slack_notifier

UNDO_WINDOW = timedelta(hours=24)
_EXECUTABLE_DECISION_STATUSES = {DecisionStatus.AUTHORIZED.value, DecisionStatus.APPROVED.value}


class ExecutionConflictError(RuntimeError):
    pass


class ExecutionNotAuthorizedError(RuntimeError):
    pass


class UndoTokenError(RuntimeError):
    pass


class ExecutionConfigurationError(ValueError):
    pass


class UndoTokenSigner:
    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("undo signing secret must be at least 32 bytes")
        self._secret = secret

    def issue(self, organization_id: str, action_id: str, expires_at: datetime) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "organization_id": organization_id,
                "action_id": action_id,
                "expires_at": int(expires_at.timestamp()),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def verify(self, token: str, *, now: datetime) -> tuple[str, str, datetime]:
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied_signature, expected):
                raise UndoTokenError("invalid undo token")
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
            if payload.get("v") != 1:
                raise UndoTokenError("unsupported undo token version")
            organization_id = payload["organization_id"]
            action_id = payload["action_id"]
            expires_at = datetime.fromtimestamp(payload["expires_at"], tz=UTC)
            if not isinstance(organization_id, str) or not isinstance(action_id, str):
                raise UndoTokenError("invalid undo token claims")
        except UndoTokenError:
            raise
        except Exception as exc:
            raise UndoTokenError("invalid undo token") from exc
        if now.astimezone(UTC) >= expires_at:
            raise UndoTokenError("undo token expired")
        return organization_id, action_id, expires_at

    @staticmethod
    def digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ExecutionStore:
    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name not in {"postgresql", "sqlite"}:
            raise ValueError("ExecutionStore supports PostgreSQL and SQLite only")
        self._engine = engine
        self._session_factory = sessionmaker(engine, expire_on_commit=False)

    def close(self) -> None:
        self._engine.dispose()

    def start(
        self,
        *,
        organization_id: str,
        action_id: str,
        tool_name: str,
        action_arguments: Mapping[str, object],
        provider: ExecutionProvider,
        now: datetime,
    ) -> tuple[ActionExecutionRow, bool]:
        arguments_fingerprint = fingerprint_action_arguments(action_arguments)
        with self._session_factory.begin() as session:
            decision = session.scalar(
                select(ActionDecisionRow)
                .where(
                    ActionDecisionRow.organization_id == organization_id,
                    ActionDecisionRow.action_id == action_id,
                )
                .with_for_update()
            )
            if decision is None:
                raise ExecutionNotAuthorizedError("policy decision not found")
            if decision.tool_name != tool_name:
                raise ExecutionNotAuthorizedError("tool does not match policy decision")
            if decision.arguments_fingerprint != arguments_fingerprint:
                raise ExecutionNotAuthorizedError("arguments do not match policy decision")
            existing = session.get(ActionExecutionRow, (organization_id, action_id))
            if existing is not None:
                if (
                    existing.tool_name != tool_name
                    or existing.arguments_fingerprint != arguments_fingerprint
                    or existing.provider != provider.value
                ):
                    raise ExecutionConflictError("execution identity was reused")
                return existing, False
            if decision.status not in _EXECUTABLE_DECISION_STATUSES:
                raise ExecutionNotAuthorizedError(
                    f"policy decision is not executable: {decision.status}"
                )
            row = ActionExecutionRow(
                organization_id=organization_id,
                action_id=action_id,
                tool_name=tool_name,
                arguments_fingerprint=arguments_fingerprint,
                provider=provider.value,
                status=ExecutionStatus.IN_PROGRESS.value,
                reversible=int(decision.risk_payload.get("reversibility_points", -1)) < 3,
                started_at=now,
                updated_at=now,
            )
            if not row.reversible:
                raise ExecutionNotAuthorizedError("phase-three tools require reversible actions")
            session.add(row)
            session.flush()
            self._append_event(session, row, "started", now)
            return row, True

    def complete(
        self,
        organization_id: str,
        action_id: str,
        *,
        external_resource_id: str,
        external_url: str | None,
        undo_expires_at: datetime,
        token_digest: str,
        now: datetime,
    ) -> ActionExecutionRow:
        with self._session_factory.begin() as session:
            row = self._locked_execution(session, organization_id, action_id)
            if row.status != ExecutionStatus.IN_PROGRESS.value:
                raise ExecutionConflictError("execution is not in progress")
            row.external_resource_id = external_resource_id
            row.external_url = external_url
            row.status = ExecutionStatus.EXECUTED.value
            row.executed_at = now
            row.undo_expires_at = undo_expires_at
            row.updated_at = now
            session.add(
                UndoTokenRow(
                    token_digest=token_digest,
                    organization_id=organization_id,
                    action_id=action_id,
                    expires_at=undo_expires_at,
                    created_at=now,
                )
            )
            decision = self._locked_decision(session, organization_id, action_id)
            decision.status = DecisionStatus.EXECUTED.value
            decision.updated_at = now
            self._append_event(session, row, "executed", now)
            return row

    def fail(
        self,
        organization_id: str,
        action_id: str,
        *,
        code: str,
        uncertain: bool,
        now: datetime,
    ) -> None:
        with self._session_factory.begin() as session:
            row = self._locked_execution(session, organization_id, action_id)
            status = ExecutionStatus.UNCERTAIN if uncertain else ExecutionStatus.FAILED
            row.status = status.value
            row.error_code = code[:100]
            row.updated_at = now
            self._append_event(session, row, status.value, now, detail_code=code[:100])

    def record_receipt(
        self,
        organization_id: str,
        action_id: str,
        *,
        channel_id: str,
        message_ts: str | None,
        error_code: str | None,
        now: datetime,
    ) -> None:
        with self._session_factory.begin() as session:
            row = self._locked_execution(session, organization_id, action_id)
            row.receipt_channel_id = channel_id
            row.receipt_message_ts = message_ts
            row.updated_at = now
            self._append_event(
                session,
                row,
                "receipt_sent" if error_code is None else "receipt_failed",
                now,
                detail_code=error_code,
            )

    def reserve_undo(
        self, token_digest: str, organization_id: str, action_id: str, *, now: datetime
    ) -> ActionExecutionRow:
        with self._session_factory.begin() as session:
            token = session.scalar(
                select(UndoTokenRow)
                .where(UndoTokenRow.token_digest == token_digest)
                .with_for_update()
            )
            if (
                token is None
                or token.organization_id != organization_id
                or token.action_id != action_id
            ):
                raise UndoTokenError("undo token not found")
            if token.consumed_at is not None:
                raise UndoTokenError("undo token already used")
            expires_at = _utc(token.expires_at)
            if expires_at is None or now >= expires_at:
                raise UndoTokenError("undo token expired")
            row = self._locked_execution(session, organization_id, action_id)
            if not row.reversible or row.status != ExecutionStatus.EXECUTED.value:
                raise ExecutionConflictError("action is not undoable")
            token.consumed_at = now
            row.status = ExecutionStatus.UNDOING.value
            row.updated_at = now
            self._append_event(session, row, "undo_started", now)
            return row

    def complete_undo(self, organization_id: str, action_id: str, *, now: datetime) -> UndoResult:
        with self._session_factory.begin() as session:
            row = self._locked_execution(session, organization_id, action_id)
            if row.status != ExecutionStatus.UNDOING.value:
                raise ExecutionConflictError("undo is not in progress")
            row.status = ExecutionStatus.UNDONE.value
            row.undone_at = now
            row.updated_at = now
            decision = self._locked_decision(session, organization_id, action_id)
            decision.status = DecisionStatus.UNDONE.value
            decision.updated_at = now
            autonomy = session.scalar(
                select(AutonomyProfileRow)
                .where(
                    AutonomyProfileRow.organization_id == organization_id,
                    AutonomyProfileRow.action_class == decision.action_class,
                )
                .with_for_update()
            )
            if autonomy is None:
                raise RuntimeError("autonomy profile not found")
            snapshot = AutonomySnapshot(
                level=AutonomyLevel(autonomy.level),
                consecutive_approvals=autonomy.consecutive_approvals,
                rejection_count=autonomy.rejection_count,
                undo_count=autonomy.undo_count,
            )
            transitioned = undo_transition(snapshot)
            autonomy.level = int(transitioned.level)
            autonomy.consecutive_approvals = transitioned.consecutive_approvals
            autonomy.rejection_count = transitioned.rejection_count
            autonomy.undo_count = transitioned.undo_count
            autonomy.updated_at = now
            self._append_event(session, row, "undone", now)
        return UndoResult(
            organization_id=organization_id,
            action_id=action_id,
            status=ExecutionStatus.UNDONE,
            undone_at=now,
        )

    def fail_undo(self, organization_id: str, action_id: str, *, code: str, now: datetime) -> None:
        with self._session_factory.begin() as session:
            row = self._locked_execution(session, organization_id, action_id)
            row.status = ExecutionStatus.UNDO_FAILED.value
            row.error_code = code[:100]
            row.updated_at = now
            self._append_event(session, row, "undo_failed", now, detail_code=code[:100])

    def get(self, organization_id: str, action_id: str) -> ActionExecutionRow | None:
        with self._session_factory() as session:
            return session.get(ActionExecutionRow, (organization_id, action_id))

    @staticmethod
    def _locked_execution(
        session: Session, organization_id: str, action_id: str
    ) -> ActionExecutionRow:
        row = session.scalar(
            select(ActionExecutionRow)
            .where(
                ActionExecutionRow.organization_id == organization_id,
                ActionExecutionRow.action_id == action_id,
            )
            .with_for_update()
        )
        if row is None:
            raise KeyError("action execution not found")
        return row

    @staticmethod
    def _locked_decision(
        session: Session, organization_id: str, action_id: str
    ) -> ActionDecisionRow:
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
        return row

    @staticmethod
    def _append_event(
        session: Session,
        row: ActionExecutionRow,
        event_type: str,
        occurred_at: datetime,
        *,
        detail_code: str | None = None,
    ) -> None:
        session.add(
            ExecutionEventRow(
                event_id=f"exe_{uuid4().hex}",
                organization_id=row.organization_id,
                action_id=row.action_id,
                event_type=event_type,
                occurred_at=occurred_at,
                detail_code=detail_code,
            )
        )


class ActionExecutionService:
    def __init__(
        self,
        store: ExecutionStore,
        signer: UndoTokenSigner,
        *,
        public_base_url: str,
        calendar: CalendarProvider,
        gmail: GmailDraftProvider,
        forms: FormProvider,
        slack: SlackNotifier | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._signer = signer
        self._public_base_url = public_base_url.rstrip("/")
        self._calendar = calendar
        self._gmail = gmail
        self._forms = forms
        self._slack = slack
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def approval_notifier(self) -> SlackNotifier | None:
        return self._slack

    def create_tentative_event(self, action: CalendarActionInput) -> ExecutionReceipt:
        return self._execute(
            action,
            tool_name="calendar_create_tentative_event",
            provider=ExecutionProvider.GOOGLE_CALENDAR,
            create=lambda: self._calendar.create_tentative_event(action),
        )

    def create_email_draft(self, action: EmailDraftActionInput) -> ExecutionReceipt:
        return self._execute(
            action,
            tool_name="gmail_create_draft",
            provider=ExecutionProvider.GMAIL,
            create=lambda: self._gmail.create_draft(action),
        )

    def create_response_request(self, action: FormActionInput) -> ExecutionReceipt:
        return self._execute(
            action,
            tool_name="forms_create_response_request",
            provider=ExecutionProvider.GOOGLE_FORMS,
            create=lambda: self._forms.create_response_request(action),
        )

    def undo(self, token: str) -> UndoResult:
        now = self._clock().astimezone(UTC)
        organization_id, action_id, _expires_at = self._signer.verify(token, now=now)
        row = self._store.reserve_undo(
            self._signer.digest(token), organization_id, action_id, now=now
        )
        if row.external_resource_id is None:
            self._store.fail_undo(
                organization_id, action_id, code="missing_external_resource_id", now=now
            )
            raise ExecutionConflictError("external resource ID is missing")
        try:
            if row.provider == ExecutionProvider.GOOGLE_CALENDAR.value:
                self._calendar.delete_event(row.external_resource_id)
            elif row.provider == ExecutionProvider.GMAIL.value:
                self._gmail.delete_draft(row.external_resource_id)
            elif row.provider == ExecutionProvider.GOOGLE_FORMS.value:
                self._forms.delete_form(row.external_resource_id)
            else:
                raise ExecutionConflictError("unsupported execution provider")
        except ProviderOperationError as exc:
            self._store.fail_undo(organization_id, action_id, code=exc.code, now=now)
            raise
        return self._store.complete_undo(organization_id, action_id, now=now)

    def _execute(
        self,
        action: CalendarActionInput | EmailDraftActionInput | FormActionInput,
        *,
        tool_name: str,
        provider: ExecutionProvider,
        create: Callable[[], tuple[str, str | None]],
    ) -> ExecutionReceipt:
        now = self._clock().astimezone(UTC)
        arguments = action.model_dump(
            mode="json",
            exclude={"organization_id", "action_id"},
            exclude_none=True,
        )
        row, acquired = self._store.start(
            organization_id=action.organization_id,
            action_id=action.action_id,
            tool_name=tool_name,
            action_arguments=arguments,
            provider=provider,
            now=now,
        )
        if not acquired:
            return self._receipt_from_existing(row)
        try:
            resource_id, external_url = create()
        except ProviderOperationError as exc:
            self._store.fail(
                action.organization_id,
                action.action_id,
                code=exc.code,
                uncertain=exc.outcome_uncertain,
                now=now,
            )
            raise
        expires_at = now + UNDO_WINDOW
        token = self._signer.issue(action.organization_id, action.action_id, expires_at)
        row = self._store.complete(
            action.organization_id,
            action.action_id,
            external_resource_id=resource_id,
            external_url=external_url,
            undo_expires_at=expires_at,
            token_digest=self._signer.digest(token),
            now=now,
        )
        receipt = self._receipt(row, token)
        channel_id = action.receipt_channel_id
        if channel_id is not None and self._slack is not None:
            try:
                message_ts = self._slack.send_group_receipt(channel_id, receipt)
                self._store.record_receipt(
                    action.organization_id,
                    action.action_id,
                    channel_id=channel_id,
                    message_ts=message_ts,
                    error_code=None,
                    now=now,
                )
            except SlackDeliveryError as exc:
                self._store.record_receipt(
                    action.organization_id,
                    action.action_id,
                    channel_id=channel_id,
                    message_ts=None,
                    error_code=exc.code,
                    now=now,
                )
        return receipt

    def _receipt_from_existing(self, row: ActionExecutionRow) -> ExecutionReceipt:
        if row.status != ExecutionStatus.EXECUTED.value:
            raise ExecutionConflictError(f"execution cannot be retried safely: {row.status}")
        expires_at = _utc(row.undo_expires_at)
        executed_at = _utc(row.executed_at)
        if expires_at is None or executed_at is None or row.external_resource_id is None:
            raise ExecutionConflictError("persisted execution receipt is incomplete")
        token = self._signer.issue(row.organization_id, row.action_id, expires_at)
        return self._receipt(row, token)

    def _receipt(self, row: ActionExecutionRow, token: str) -> ExecutionReceipt:
        executed_at = _utc(row.executed_at)
        expires_at = _utc(row.undo_expires_at)
        if executed_at is None or expires_at is None or row.external_resource_id is None:
            raise ExecutionConflictError("execution receipt is incomplete")
        undo_url = f"{self._public_base_url}/actions/undo?token={quote(token, safe='')}"
        return ExecutionReceipt(
            organization_id=row.organization_id,
            action_id=row.action_id,
            tool_name=row.tool_name,
            provider=ExecutionProvider(row.provider),
            external_resource_id=row.external_resource_id,
            external_url=row.external_url,
            status=ExecutionStatus.EXECUTED,
            reversible=row.reversible,
            executed_at=executed_at,
            undo_expires_at=expires_at,
            undo_url=undo_url,
        )


def undo_signer_from_environment() -> UndoTokenSigner:
    raw = os.environ.get("QUORUM_UNDO_SIGNING_SECRET", "")
    if not raw:
        raise RuntimeError("QUORUM_UNDO_SIGNING_SECRET is required")
    return UndoTokenSigner(raw.encode("utf-8"))


def build_action_execution_service(engine: Engine) -> ActionExecutionService:
    """Build the credentialed production execution path from environment configuration."""

    public_base_url = os.environ.get("QUORUM_PUBLIC_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(public_base_url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ExecutionConfigurationError(
            "QUORUM_PUBLIC_BASE_URL must be an absolute HTTPS origin without query or fragment"
        )
    signer = undo_signer_from_environment()
    slack = build_slack_notifier()
    calendar, gmail, forms = build_google_providers()
    return ActionExecutionService(
        ExecutionStore(engine),
        signer,
        public_base_url=public_base_url,
        calendar=calendar,
        gmail=gmail,
        forms=forms,
        slack=slack,
    )


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
