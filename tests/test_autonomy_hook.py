from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from unittest.mock import Mock

from alembic import command
from alembic.config import Config
from strands.hooks import BeforeToolCallEvent, HookRegistry
from strands.interrupt import _InterruptState
from strands.types.tools import ToolUse

from quorum.autonomy_hook import POLICY_INVOCATION_STATE_KEY, QuorumAutonomyGate
from quorum.database import DatabaseSettings, create_database_engine
from quorum.decision_store import DecisionPolicyStore
from quorum.models import (
    ActionRequest,
    AutonomySnapshot,
    DecisionStatus,
    ImpactRadius,
    MoneyImpact,
    PolicyDecision,
    Reversibility,
    RiskAssessment,
    RiskTier,
    TaskClass,
    TimeoutDefault,
)
from quorum.policy import fingerprint_action_arguments

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_decision(
    *, status: DecisionStatus, selected: list[str], tool_name: str = "calendar_create_event"
) -> PolicyDecision:
    return PolicyDecision(
        action_id="action_test",
        organization_id="org_test",
        requested_by_id="person_requester",
        action_class=TaskClass.EVENT_DECISION,
        tool_name=tool_name,
        arguments_fingerprint=fingerprint_action_arguments({"title": "Planning"}),
        risk=RiskAssessment(
            score=0,
            tier=RiskTier.LOW,
            reversibility_points=0,
            impact_radius_points=0,
            money_impact_points=0,
            reasons=[
                "reversibility:reversible=0",
                "impact_radius:individual=0",
                "money_impact:none=0",
            ],
        ),
        autonomy=AutonomySnapshot(),
        required_quorum=len(selected),
        selected_decider_ids=selected,
        status=status,
        timeout_at=(
            datetime(2026, 8, 27, 10, tzinfo=UTC)
            if status is DecisionStatus.AWAITING_APPROVAL
            else None
        ),
        timeout_default=TimeoutDefault.EXECUTE_AND_NOTIFY,
    )


def make_event(
    decision: PolicyDecision | dict[str, Any] | None,
    *,
    include_action_identity: bool = False,
) -> BeforeToolCallEvent:
    agent = Mock()
    agent._interrupt_state = _InterruptState()
    invocation_state = {} if decision is None else {POLICY_INVOCATION_STATE_KEY: decision}
    tool_use = cast(
        ToolUse,
        {
            "toolUseId": "tool_use_test",
            "name": "calendar_create_event",
            "input": {
                "title": "Planning",
                **(
                    {"organization_id": "org_test", "action_id": "action_test"}
                    if include_action_identity
                    else {}
                ),
            },
        },
    )
    return BeforeToolCallEvent(
        agent=agent,
        selected_tool=None,
        tool_use=tool_use,
        invocation_state=invocation_state,
    )


class QuorumAutonomyGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_policy_cancels_tool(self) -> None:
        registry = HookRegistry()
        registry.add_hook(QuorumAutonomyGate())
        event = make_event(None)

        updated, interrupts = await registry.invoke_callbacks_async(event)

        self.assertEqual(updated.cancel_tool, "Quorum policy decision is required")
        self.assertEqual(interrupts, [])

    async def test_authorized_action_executes_without_interrupt(self) -> None:
        registry = HookRegistry()
        registry.add_hook(QuorumAutonomyGate())
        event = make_event(make_decision(status=DecisionStatus.AUTHORIZED, selected=[]))

        updated, interrupts = await registry.invoke_callbacks_async(event)

        self.assertFalse(updated.cancel_tool)
        self.assertEqual(interrupts, [])

    async def test_tool_mismatch_cancels_tool(self) -> None:
        registry = HookRegistry()
        registry.add_hook(QuorumAutonomyGate())
        event = make_event(
            make_decision(
                status=DecisionStatus.AUTHORIZED,
                selected=[],
                tool_name="email_send_draft",
            )
        )

        updated, interrupts = await registry.invoke_callbacks_async(event)

        self.assertEqual(updated.cancel_tool, "Tool call does not match the authorized action")
        self.assertEqual(interrupts, [])

    async def test_argument_mismatch_cancels_tool(self) -> None:
        registry = HookRegistry()
        registry.add_hook(QuorumAutonomyGate())
        event = make_event(make_decision(status=DecisionStatus.AUTHORIZED, selected=[]))
        event.tool_use["input"]["title"] = "Changed after approval"

        updated, interrupts = await registry.invoke_callbacks_async(event)

        self.assertEqual(updated.cancel_tool, "Tool arguments do not match the authorized action")
        self.assertEqual(interrupts, [])

    async def test_two_person_quorum_uses_two_native_interrupts(self) -> None:
        registry = HookRegistry()
        registry.add_hook(QuorumAutonomyGate())
        event = make_event(
            make_decision(
                status=DecisionStatus.AWAITING_APPROVAL,
                selected=["person_a", "person_b"],
            )
        )

        _, interrupts = await registry.invoke_callbacks_async(event)

        self.assertEqual(len(interrupts), 2)
        self.assertEqual(
            {interrupt.reason["participant_id"] for interrupt in interrupts},
            {"person_a", "person_b"},
        )

    async def test_private_question_is_sent_once_before_native_interrupt(self) -> None:
        sender = Mock()
        sender.send_private_question.return_value = "1780000000.000100"
        registry = HookRegistry()
        registry.add_hook(QuorumAutonomyGate(question_sender=sender))
        event = make_event(
            make_decision(status=DecisionStatus.AWAITING_APPROVAL, selected=["person_a"])
        )

        _, interrupts = await registry.invoke_callbacks_async(event)
        sender.send_private_question.assert_called_once()
        event.agent._interrupt_state.interrupts[interrupts[0].id].response = "approve"

        updated, resumed_interrupts = await registry.invoke_callbacks_async(event)

        self.assertEqual(resumed_interrupts, [])
        self.assertFalse(updated.cancel_tool)
        sender.send_private_question.assert_called_once()

    async def test_resumed_rejection_cancels_tool(self) -> None:
        resolver = Mock()
        resolver.get_decision.return_value = make_decision(
            status=DecisionStatus.AWAITING_APPROVAL, selected=["person_a"]
        )
        resolver.resolve.return_value = DecisionStatus.REJECTED
        registry = HookRegistry()
        registry.add_hook(
            QuorumAutonomyGate(resolver, clock=lambda: datetime(2026, 8, 26, 10, tzinfo=UTC))
        )
        event = make_event(
            None,
            include_action_identity=True,
        )
        _, interrupts = await registry.invoke_callbacks_async(event)
        event.agent._interrupt_state.interrupts[interrupts[0].id].response = "reject"

        updated, resumed_interrupts = await registry.invoke_callbacks_async(event)

        self.assertEqual(resumed_interrupts, [])
        self.assertEqual(updated.cancel_tool, "Quorum approval denied")
        resolution = resolver.resolve.call_args.args[1]
        self.assertEqual(resolution.responses[0].participant_id, "person_a")
        self.assertEqual(resolution.responses[0].decision, "reject")
        resolver.get_decision.assert_called_with("org_test", "action_test")

    async def test_two_resumed_native_interrupts_accumulate_into_approval(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hook.sqlite3"
            engine = create_database_engine(DatabaseSettings(url=f"sqlite+pysqlite:///{path}"))
            config = Config(PROJECT_ROOT / "alembic.ini")
            config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
            store = DecisionPolicyStore(engine)
            try:
                decision = store.decide(
                    ActionRequest(
                        action_id="action_test",
                        organization_id="org_test",
                        requested_by_id="person_requester",
                        action_class=TaskClass.EVENT_DECISION,
                        tool_name="calendar_create_event",
                        summary="Publish an irreversible external event",
                        reversibility=Reversibility.IRREVERSIBLE,
                        impact_radius=ImpactRadius.EXTERNAL,
                        money_impact=MoneyImpact.NONE,
                        candidate_decider_ids=["person_a", "person_b"],
                        action_arguments={"title": "Planning"},
                        requested_at=datetime(2026, 8, 26, 10, tzinfo=UTC),
                    ),
                    now=datetime(2026, 8, 26, 10, tzinfo=UTC),
                )
                self.assertEqual(decision.required_quorum, 2)
                registry = HookRegistry()
                registry.add_hook(
                    QuorumAutonomyGate(store, clock=lambda: datetime(2026, 8, 26, 10, tzinfo=UTC))
                )
                event = make_event(None, include_action_identity=True)

                _, interrupts = await registry.invoke_callbacks_async(event)
                for interrupt in interrupts:
                    event.agent._interrupt_state.interrupts[interrupt.id].response = "approve"

                updated, resumed_interrupts = await registry.invoke_callbacks_async(event)

                self.assertEqual(resumed_interrupts, [])
                self.assertFalse(updated.cancel_tool)
                persisted = store.get_decision("org_test", "action_test")
                self.assertIsNotNone(persisted)
                self.assertIs(persisted.status, DecisionStatus.APPROVED)
            finally:
                store.close()

    async def test_overdue_high_risk_action_expires_before_new_interrupt(self) -> None:
        resolver = Mock()
        awaiting = make_decision(status=DecisionStatus.AWAITING_APPROVAL, selected=["person_a"])
        expired = awaiting.model_copy(update={"status": DecisionStatus.EXPIRED})
        decisions = iter([awaiting, *([expired] * 10)])
        resolver.get_decision.side_effect = lambda *_args: next(decisions)
        resolver.resolve_timeout.return_value = DecisionStatus.EXPIRED
        registry = HookRegistry()
        registry.add_hook(
            QuorumAutonomyGate(resolver, clock=lambda: datetime(2026, 8, 28, 10, tzinfo=UTC))
        )
        event = make_event(None, include_action_identity=True)

        updated, interrupts = await registry.invoke_callbacks_async(event)

        self.assertEqual(interrupts, [])
        self.assertEqual(updated.cancel_tool, "Action is not executable: expired")
        resolver.resolve_timeout.assert_called_with(
            "org_test",
            "action_test",
            now=datetime(2026, 8, 28, 10, tzinfo=UTC),
        )


if __name__ == "__main__":
    unittest.main()
