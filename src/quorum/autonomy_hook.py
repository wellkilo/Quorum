"""Strands-native interrupt gate for deterministic Quorum policy decisions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from strands.hooks import BeforeToolCallEvent, HookRegistry

from quorum.models import (
    DecisionStatus,
    InterruptResolution,
    ParticipantResponse,
    PolicyDecision,
)

POLICY_INVOCATION_STATE_KEY = "quorum_policy_decision"
MAX_QUORUM_SIZE = 10


class InterruptResolver(Protocol):
    def get_decision(self, organization_id: str, action_id: str) -> PolicyDecision | None: ...

    def resolve(
        self,
        organization_id: str,
        resolution: InterruptResolution,
        *,
        now: datetime | None = None,
    ) -> DecisionStatus: ...

    def resolve_timeout(
        self, organization_id: str, action_id: str, *, now: datetime | None = None
    ) -> DecisionStatus: ...


class QuorumAutonomyGate:
    """Allow, interrupt, or cancel a tool call using a host-provided policy decision."""

    def __init__(
        self,
        resolver: InterruptResolver | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._resolver = resolver
        self._clock = clock or (lambda: datetime.now(UTC))

    def register_hooks(self, registry: HookRegistry, **_kwargs: Any) -> None:
        for slot in range(MAX_QUORUM_SIZE):
            registry.add_callback(
                BeforeToolCallEvent,
                self._approval_callback(slot),
            )

    def _approval_callback(self, slot: int) -> Any:
        def authorize(event: BeforeToolCallEvent) -> None:
            decision = self._decision_for_event(event)
            if decision is None:
                event.cancel_tool = "Quorum policy decision is required"
                return
            if event.tool_use["name"] != decision.tool_name:
                event.cancel_tool = "Tool call does not match the authorized action"
                return
            if decision.status in {DecisionStatus.AUTHORIZED, DecisionStatus.APPROVED}:
                return
            if decision.status is not DecisionStatus.AWAITING_APPROVAL:
                event.cancel_tool = f"Action is not executable: {decision.status.value}"
                return
            if slot >= len(decision.selected_decider_ids):
                return

            participant_id = decision.selected_decider_ids[slot]
            response = event.interrupt(
                f"quorum-approval-{slot}",
                reason={
                    "action_id": decision.action_id,
                    "participant_id": participant_id,
                    "risk_tier": decision.risk.tier.value,
                    "required_quorum": decision.required_quorum,
                    "timeout_at": (
                        decision.timeout_at.isoformat() if decision.timeout_at is not None else None
                    ),
                    "timeout_default": decision.timeout_default.value,
                },
            )
            approved = _response_approved(response)
            if self._resolver is not None:
                resolution_status = self._resolver.resolve(
                    decision.organization_id,
                    InterruptResolution(
                        action_id=decision.action_id,
                        responses=[
                            ParticipantResponse(
                                participant_id=participant_id,
                                decision="approve" if approved else "reject",
                            )
                        ],
                    ),
                )
                if resolution_status is DecisionStatus.REJECTED:
                    event.cancel_tool = "Quorum approval denied"
                    return
            if not approved:
                event.cancel_tool = "Quorum approval denied"

        return authorize

    def _decision_for_event(self, event: BeforeToolCallEvent) -> PolicyDecision | None:
        tool_input = event.tool_use.get("input", {})
        organization_id = tool_input.get("organization_id")
        action_id = tool_input.get("action_id")
        if (
            self._resolver is not None
            and isinstance(organization_id, str)
            and isinstance(action_id, str)
        ):
            decision = self._resolver.get_decision(organization_id, action_id)
            if (
                decision is not None
                and decision.status
                in {DecisionStatus.AWAITING_APPROVAL, DecisionStatus.DEFERRED_BUDGET}
                and decision.timeout_at is not None
                and self._clock().astimezone(UTC) >= decision.timeout_at.astimezone(UTC)
            ):
                self._resolver.resolve_timeout(
                    organization_id, action_id, now=self._clock().astimezone(UTC)
                )
                decision = self._resolver.get_decision(organization_id, action_id)
            return decision
        raw_decision = event.invocation_state.get(POLICY_INVOCATION_STATE_KEY)
        if raw_decision is None:
            return None
        return (
            raw_decision
            if isinstance(raw_decision, PolicyDecision)
            else PolicyDecision.model_validate(raw_decision)
        )


def _response_approved(response: Any) -> bool:
    if isinstance(response, str):
        return response.strip().casefold() in {"approve", "approved", "yes", "y"}
    if isinstance(response, dict):
        decision = response.get("decision")
        return isinstance(decision, str) and decision.strip().casefold() in {
            "approve",
            "approved",
            "yes",
            "y",
        }
    return False
