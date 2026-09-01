from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from slack_sdk.socket_mode.request import SocketModeRequest

from quorum.models import (
    DataClassification,
    ExecutionReceipt,
    PolicyDecision,
    WeeklySummary,
)
from quorum.slack_ingress import SlackEventConverter
from quorum.slack_live_evidence import (
    SYNTHETIC_TRANSPORT_MARKER,
    SlackLiveEvidenceError,
    main,
    require_closed_cost_gates,
    run_slack_live_evidence,
    write_evidence_report,
)

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


class FakeSocketClient:
    def __init__(
        self, text: str = SYNTHETIC_TRANSPORT_MARKER, channel_id: str = "C_PRIVATE"
    ) -> None:
        self.text = text
        self.channel_id = channel_id
        self.socket_mode_request_listeners: list[object] = []
        self.acknowledgements: list[dict[str, object]] = []
        self.connected = False
        self.disconnected = False

    def connect(self) -> None:
        self.connected = True
        request = SocketModeRequest(
            type="events_api",
            envelope_id="envelope_private",
            payload={
                "type": "event_callback",
                "team_id": "T_PRIVATE",
                "event": {
                    "type": "message",
                    "channel": self.channel_id,
                    "user": "U_PRIVATE",
                    "ts": "1770000000.000100",
                    "text": self.text,
                },
            },
        )
        for listener in self.socket_mode_request_listeners:
            listener(self, request)  # type: ignore[operator]

    def disconnect(self) -> None:
        self.disconnected = True

    def send_socket_mode_response(self, response: object) -> None:
        self.acknowledgements.append(response.to_dict())  # type: ignore[attr-defined]


class FakeSurfaceSender:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def send_group_receipt(
        self,
        channel_id: str,
        receipt: ExecutionReceipt,
        *,
        data_classification: DataClassification | None = None,
    ) -> str:
        self.calls.append("group_receipt")
        return "1770000001.000100"

    def send_private_question(
        self,
        participant_id: str,
        decision: PolicyDecision,
        *,
        data_classification: DataClassification | None = None,
    ) -> str:
        self.calls.append("private_question")
        return "1770000002.000100"

    def send_weekly_summary(self, channel_id: str, summary: WeeklySummary) -> str:
        self.calls.append("weekly_summary")
        return "1770000003.000100"


class SlackLiveEvidenceTest(unittest.TestCase):
    def test_marker_ack_then_three_surfaces_produces_pii_safe_report(self) -> None:
        client = FakeSocketClient()
        sender = FakeSurfaceSender()

        report = run_slack_live_evidence(
            client=client,  # type: ignore[arg-type]
            converter=SlackEventConverter(b"live-evidence-pseudonym-key"),
            sender=sender,
            channel_id="C_PRIVATE",
            participant_id="U_PRIVATE",
            timeout_seconds=0.1,
            now=NOW,
        )

        self.assertTrue(client.connected)
        self.assertTrue(client.disconnected)
        self.assertEqual(client.acknowledgements, [{"envelope_id": "envelope_private"}])
        self.assertEqual(sender.calls, ["group_receipt", "private_question", "weekly_summary"])
        self.assertEqual(report.messages_sent, 3)
        self.assertEqual(report.web_api_responses_validated, 4)
        self.assertEqual(report.external_side_effect_calls, 4)
        self.assertEqual(report.model_calls, 0)
        self.assertEqual(report.gateway_tool_calls, 0)
        serialized = report.model_dump_json()
        for forbidden in (
            "T_PRIVATE",
            "C_PRIVATE",
            "U_PRIVATE",
            "envelope_private",
            "1770000000.000100",
            SYNTHETIC_TRANSPORT_MARKER,
        ):
            self.assertNotIn(forbidden, serialized)

    def test_nonmatching_marker_times_out_after_ack_without_posts(self) -> None:
        client = FakeSocketClient("A different synthetic sentence")
        sender = FakeSurfaceSender()

        with self.assertRaisesRegex(SlackLiveEvidenceError, "marker_timeout"):
            run_slack_live_evidence(
                client=client,  # type: ignore[arg-type]
                converter=SlackEventConverter(b"live-evidence-pseudonym-key"),
                sender=sender,
                channel_id="C_PRIVATE",
                participant_id="U_PRIVATE",
                timeout_seconds=0.001,
                now=NOW,
            )

        self.assertEqual(len(client.acknowledgements), 1)
        self.assertTrue(client.disconnected)
        self.assertEqual(sender.calls, [])

    def test_matching_marker_from_another_channel_does_not_trigger_posts(self) -> None:
        client = FakeSocketClient(channel_id="C_OTHER")
        sender = FakeSurfaceSender()

        with self.assertRaisesRegex(SlackLiveEvidenceError, "marker_timeout"):
            run_slack_live_evidence(
                client=client,  # type: ignore[arg-type]
                converter=SlackEventConverter(b"live-evidence-pseudonym-key"),
                sender=sender,
                channel_id="C_PRIVATE",
                participant_id="U_PRIVATE",
                timeout_seconds=0.001,
                now=NOW,
            )

        self.assertEqual(sender.calls, [])

    def test_cost_gates_must_remain_closed(self) -> None:
        require_closed_cost_gates({})
        require_closed_cost_gates(
            {"QUORUM_BEDROCK_ENABLED": "false", "QUORUM_EXECUTION_ENABLED": "0"}
        )
        for name in ("QUORUM_BEDROCK_ENABLED", "QUORUM_EXECUTION_ENABLED"):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, name):
                require_closed_cost_gates({name: "true"})

    def test_preview_requires_no_credentials_or_clients(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("quorum.slack_live_evidence.build_slack_socket_client") as client,
            patch("builtins.print") as output,
        ):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        client.assert_not_called()
        payload = json.loads(output.call_args.args[0])
        self.assertEqual(payload["status"], "preview")
        self.assertEqual(payload["messages_to_send_after_marker"], 3)
        self.assertTrue(payload["visual_inspection_required"])

    def test_confirmed_cli_rejects_open_cost_gate_before_building_clients(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {
                    "QUORUM_BEDROCK_ENABLED": "true",
                    "QUORUM_EXECUTION_ENABLED": "false",
                },
                clear=True,
            ),
            patch("quorum.slack_live_evidence.build_slack_socket_client") as client,
            self.assertRaises(SystemExit),
        ):
            main(["--confirm-live-posts"])

        client.assert_not_called()

    def test_public_contract_documents_report_boundary_and_manual_inspection(self) -> None:
        root = Path(__file__).resolve().parents[1]
        api = (root / "API.md").read_text(encoding="utf-8")
        method = (root / "Method.md").read_text(encoding="utf-8")
        setup = (root / "docs/slack/setup.md").read_text(encoding="utf-8")

        self.assertIn("status=provider_responses_validated", api)
        self.assertIn("manual inspection remains required", method)
        self.assertIn("--output reports/slack-live-evidence.json", setup)
        self.assertIn("Neither result is a real-world impact study", setup)

    def test_report_file_is_new_and_contains_no_slack_identifiers(self) -> None:
        report = run_slack_live_evidence(
            client=FakeSocketClient(),  # type: ignore[arg-type]
            converter=SlackEventConverter(b"live-evidence-pseudonym-key"),
            sender=FakeSurfaceSender(),
            channel_id="C_PRIVATE",
            participant_id="U_PRIVATE",
            timeout_seconds=0.1,
            now=NOW,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence.json"
            write_evidence_report(output, report.model_dump_json(indent=2))
            payload = json.loads(output.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(RuntimeError, "must not overwrite"):
                write_evidence_report(output, "{}")

        self.assertEqual(payload["status"], "provider_responses_validated")
        self.assertTrue(payload["envelope_ack_sent"])
        self.assertEqual(payload["canonical_event_classification"], "synthetic")
        self.assertEqual(payload["messages_sent"], 3)
        self.assertEqual(payload["web_api_responses_validated"], 4)
        self.assertNotIn("channel_id", payload)
        self.assertNotIn("participant_id", payload)
        self.assertNotIn("message", payload)


if __name__ == "__main__":
    unittest.main()
