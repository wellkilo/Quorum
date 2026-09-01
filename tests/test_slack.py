from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any

from slack_sdk.errors import SlackApiError

from quorum.models import (
    AutonomySnapshot,
    DataClassification,
    DecisionStatus,
    ExecutionProvider,
    ExecutionReceipt,
    ExecutionStatus,
    PolicyDecision,
    RiskAssessment,
    RiskTier,
    TaskClass,
    TimeoutDefault,
    WeeklySummary,
)
from quorum.slack import SlackDeliveryError, SlackNotifier

NOW = datetime(2026, 8, 27, 9, tzinfo=UTC)


class FakeSlackClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def conversations_open(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("conversations_open", kwargs))
        return {"ok": True, "channel": {"id": "D_PRIVATE"}}

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("chat_postMessage", kwargs))
        return {"ok": True, "ts": "1780000000.000100"}


def make_receipt() -> ExecutionReceipt:
    return ExecutionReceipt(
        organization_id="org_test",
        action_id="action_test",
        tool_name="calendar_create_tentative_event",
        provider=ExecutionProvider.GOOGLE_CALENDAR,
        external_resource_id="event_123",
        external_url="https://calendar.google.com/event?eid=opaque",
        status=ExecutionStatus.EXECUTED,
        reversible=True,
        executed_at=NOW,
        undo_expires_at=NOW,
        undo_url="https://demo.example/actions/undo?token=opaque",
    )


def make_decision() -> PolicyDecision:
    return PolicyDecision(
        action_id="action_test",
        organization_id="org_test",
        requested_by_id="person_requester",
        action_class=TaskClass.EVENT_DECISION,
        tool_name="calendar_create_tentative_event",
        arguments_fingerprint="a" * 64,
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
        required_quorum=1,
        selected_decider_ids=["person_a"],
        status=DecisionStatus.AWAITING_APPROVAL,
        timeout_at=NOW,
        timeout_default=TimeoutDefault.EXECUTE_AND_NOTIFY,
    )


class SlackNotifierTest(unittest.TestCase):
    def test_group_receipt_is_one_line_with_open_and_undo_buttons(self) -> None:
        client = FakeSlackClient()

        timestamp = SlackNotifier(client).send_group_receipt("C_GROUP", make_receipt())

        self.assertEqual(timestamp, "1780000000.000100")
        method, payload = client.calls[0]
        self.assertEqual(method, "chat_postMessage")
        self.assertEqual(payload["channel"], "C_GROUP")
        self.assertNotIn("\n", payload["text"])
        self.assertEqual([block["type"] for block in payload["blocks"]], ["section", "actions"])
        buttons = payload["blocks"][1]["elements"]
        self.assertEqual([button["text"]["text"] for button in buttons], ["Open", "Undo"])
        self.assertEqual(buttons[1]["url"], make_receipt().undo_url)

    def test_synthetic_group_receipt_is_visibly_labeled(self) -> None:
        client = FakeSlackClient()

        SlackNotifier(client).send_group_receipt(
            "C_GROUP",
            make_receipt(),
            data_classification=DataClassification.SYNTHETIC,
        )

        payload = client.calls[0][1]
        self.assertTrue(payload["text"].startswith("Synthetic demo —"))
        buttons = payload["blocks"][1]["elements"]
        self.assertEqual(
            [button["text"]["text"] for button in buttons],
            ["Open demo", "Undo preview"],
        )

    def test_private_question_opens_a_dm_then_posts_timeout_and_default(self) -> None:
        client = FakeSlackClient()

        timestamp = SlackNotifier(client).send_private_question("person_a", make_decision())

        self.assertEqual(timestamp, "1780000000.000100")
        self.assertEqual(client.calls[0], ("conversations_open", {"users": "person_a"}))
        method, payload = client.calls[1]
        self.assertEqual(method, "chat_postMessage")
        self.assertEqual(payload["channel"], "D_PRIVATE")
        self.assertIn("2026-08-27T09:00:00+00:00", payload["text"])
        self.assertIn("execute_and_notify", payload["text"])

    def test_weekly_summary_is_one_message_with_interrupt_budget_evidence(self) -> None:
        client = FakeSlackClient()
        summary = WeeklySummary(
            organization_id="org_test",
            week_ending=NOW.date(),
            closed_decisions=6,
            decision_latency_p50_hours=7.0,
            interruption_count=6,
            people_interrupted=3,
            max_interruptions_per_person=2,
            undo_rate=1 / 6,
            data_classification=DataClassification.SYNTHETIC,
        )

        timestamp = SlackNotifier(client).send_weekly_summary("C_GROUP", summary)

        self.assertEqual(timestamp, "1780000000.000100")
        self.assertEqual(len(client.calls), 1)
        method, payload = client.calls[0]
        self.assertEqual(method, "chat_postMessage")
        self.assertEqual(payload["channel"], "C_GROUP")
        self.assertTrue(payload["text"].startswith("Synthetic demo —"))
        self.assertEqual(
            [block["type"] for block in payload["blocks"]],
            ["header", "section", "context"],
        )
        field_text = " ".join(field["text"] for field in payload["blocks"][1]["fields"])
        self.assertIn("*Total interruptions*\n6", field_text)
        self.assertIn("*Max per person*\n2/2", field_text)
        context = payload["blocks"][2]["elements"][0]["text"]
        self.assertIn("not a measured real-world outcome", context)

    def test_weekly_summary_rejects_an_interrupt_budget_violation(self) -> None:
        with self.assertRaisesRegex(ValueError, "exceeds the per-person interrupt budget"):
            WeeklySummary(
                organization_id="org_test",
                week_ending=NOW.date(),
                closed_decisions=6,
                decision_latency_p50_hours=7.0,
                interruption_count=6,
                people_interrupted=3,
                max_interruptions_per_person=3,
                undo_rate=0.0,
                data_classification=DataClassification.SYNTHETIC,
            )

    def test_weekly_summary_rejects_an_impossible_total_interrupt_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot fit within the per-person budget"):
            WeeklySummary(
                organization_id="org_test",
                week_ending=NOW.date(),
                closed_decisions=6,
                decision_latency_p50_hours=7.0,
                interruption_count=6,
                people_interrupted=2,
                max_interruptions_per_person=2,
                undo_rate=0.0,
                data_classification=DataClassification.SYNTHETIC,
            )

    def test_slack_error_exposes_only_a_safe_code(self) -> None:
        secret_token = "synthetic-token-that-must-not-leak"
        private_message = "private volunteer text"

        class FailingClient:
            def chat_postMessage(self, **_kwargs: Any) -> dict[str, Any]:
                raise SlackApiError(
                    f"request contained {secret_token} and {private_message}",
                    {"error": "channel_not_found"},
                )

        with self.assertRaises(SlackDeliveryError) as raised:
            SlackNotifier(FailingClient()).send_group_receipt("C_GROUP", make_receipt())

        self.assertEqual(str(raised.exception), "channel_not_found")
        self.assertNotIn(secret_token, str(raised.exception))
        self.assertNotIn(private_message, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
