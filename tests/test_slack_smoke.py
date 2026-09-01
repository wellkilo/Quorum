from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from quorum.models import DataClassification, ExecutionReceipt, PolicyDecision, WeeklySummary
from quorum.slack_smoke import load_synthetic_snapshot, main, run_slack_surface_smoke

NOW = datetime(2026, 9, 1, 8, tzinfo=UTC)


class FakeSurfaceSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object, DataClassification | None]] = []

    def send_group_receipt(
        self,
        channel_id: str,
        receipt: ExecutionReceipt,
        *,
        data_classification: DataClassification | None = None,
    ) -> str:
        self.calls.append(("receipt", channel_id, receipt, data_classification))
        return "1800000000.000100"

    def send_private_question(
        self,
        participant_id: str,
        decision: PolicyDecision,
        *,
        data_classification: DataClassification | None = None,
    ) -> str:
        self.calls.append(("question", participant_id, decision, data_classification))
        return "1800000000.000200"

    def send_weekly_summary(self, channel_id: str, summary: WeeklySummary) -> str:
        self.calls.append(("summary", channel_id, summary, summary.data_classification))
        return "1800000000.000300"


class SlackSurfaceSmokeTest(unittest.TestCase):
    def test_runner_sends_exactly_three_synthetic_surfaces_without_execution(self) -> None:
        sender = FakeSurfaceSender()

        result = run_slack_surface_smoke(
            sender,
            channel_id="C_DEMO",
            participant_id="U_DEMO",
            snapshot=load_synthetic_snapshot(),
            now=NOW,
        )

        self.assertEqual([call[0] for call in sender.calls], ["receipt", "question", "summary"])
        self.assertEqual([call[1] for call in sender.calls], ["C_DEMO", "U_DEMO", "C_DEMO"])
        self.assertTrue(all(call[3] is DataClassification.SYNTHETIC for call in sender.calls))
        receipt = sender.calls[0][2]
        self.assertIsInstance(receipt, ExecutionReceipt)
        self.assertEqual(receipt.external_resource_id, "synthetic_preview_only")
        self.assertEqual(result.messages_sent, 3)
        self.assertEqual(result.model_calls, 0)
        self.assertEqual(result.execution_tool_calls, 0)

    def test_preview_requires_no_slack_token_and_sends_nothing(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("quorum.slack_smoke.build_slack_notifier") as notifier,
            patch("builtins.print") as output,
        ):
            status = main([])

        self.assertEqual(status, 0)
        notifier.assert_not_called()
        payload = json.loads(output.call_args.args[0])
        self.assertEqual(payload["status"], "preview")
        self.assertEqual(payload["data_classification"], "synthetic")
        self.assertEqual(payload["messages_to_send"], 3)
        self.assertEqual(payload["model_calls"], 0)

    def test_loader_rejects_a_fixture_not_marked_synthetic(self) -> None:
        fixture = load_synthetic_snapshot().model_dump(mode="json")
        fixture["data_classification"] = "redacted-real"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.json"
            path.write_text(json.dumps(fixture), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "requires a synthetic fixture"):
                load_synthetic_snapshot(path)

    def test_public_contract_documents_the_explicit_live_post_gate(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        api = (root / "API.md").read_text(encoding="utf-8")
        method = (root / "Method.md").read_text(encoding="utf-8")
        implementation = inspect.getsource(main)

        self.assertIn("uv run quorum-slack-smoke --confirm-live-posts", readme)
        self.assertIn("exactly three outbound Slack interactions", api)
        self.assertIn("zero model calls and zero execution-tool calls", api)
        self.assertIn("A live credentialed workspace test is still outstanding", method)
        self.assertIn("if not args.confirm_live_posts", implementation)


if __name__ == "__main__":
    unittest.main()
