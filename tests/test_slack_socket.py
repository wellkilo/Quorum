from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError
from slack_sdk.socket_mode.request import SocketModeRequest

from quorum.models import CanonicalMessageEvent, DataClassification
from quorum.slack_ingress import SlackEventConverter
from quorum.slack_socket import (
    DEFAULT_MANIFEST,
    SlackSocketModeBridge,
    validate_slack_manifest,
)


class FakeSocketClient:
    def __init__(self) -> None:
        self.acknowledgements: list[dict[str, object]] = []
        self.order: list[str] = []

    def send_socket_mode_response(self, response: object) -> None:
        self.order.append("ack")
        self.acknowledgements.append(response.to_dict())  # type: ignore[attr-defined]


def socket_request(payload: dict[str, object]) -> SocketModeRequest:
    return SocketModeRequest(type="events_api", envelope_id="envelope_opaque", payload=payload)


class SlackManifestContractTest(unittest.TestCase):
    def test_manifest_has_only_reviewed_scopes_event_and_socket_mode(self) -> None:
        manifest = validate_slack_manifest()

        self.assertEqual(
            set(manifest.oauth_config.scopes.bot),
            {"channels:history", "chat:write", "im:write"},
        )
        self.assertEqual(manifest.settings.event_subscriptions.bot_events, ["message.channels"])
        self.assertTrue(manifest.settings.socket_mode_enabled)

    def test_manifest_rejects_extra_scope_and_webhook_url(self) -> None:
        manifest = json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8"))
        manifest["oauth_config"]["scopes"]["bot"].append("users:read")
        manifest["settings"]["event_subscriptions"]["request_url"] = "https://example.invalid/slack"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(ValidationError):
                validate_slack_manifest(path)


class SlackSocketModeBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeSocketClient()
        self.converter = SlackEventConverter(b"socket-mode-pseudonym-key")
        self.events: list[CanonicalMessageEvent] = []

    def _payload(self) -> dict[str, object]:
        return {
            "type": "event_callback",
            "team_id": "T12345",
            "event": {
                "type": "message",
                "channel": "C12345",
                "user": "U12345",
                "ts": "1770000000.000100",
                "text": "Ask volunteer@example.org; I will bring the keys.",
            },
        }

    def test_acknowledges_before_processing_and_passes_only_redacted_event(self) -> None:
        async def processor(event: CanonicalMessageEvent) -> dict[str, object]:
            self.client.order.append("process")
            self.events.append(event)
            return {"accepted": True}

        dispatch = SlackSocketModeBridge(converter=self.converter, processor=processor).dispatch(
            self.client, socket_request(self._payload())
        )

        self.assertEqual(self.client.order, ["ack", "process"])
        self.assertEqual(self.client.acknowledgements, [{"envelope_id": "envelope_opaque"}])
        self.assertEqual(dispatch.status, "accepted")
        self.assertIs(dispatch.data_classification, DataClassification.REDACTED_REAL)
        self.assertEqual(len(self.events), 1)
        serialized = self.events[0].model_dump_json()
        self.assertNotIn("T12345", serialized)
        self.assertNotIn("C12345", serialized)
        self.assertNotIn("U12345", serialized)
        self.assertNotIn("volunteer@example.org", serialized)
        self.assertIn("<EMAIL_REDACTED>", self.events[0].text)

    def test_acknowledges_ignored_and_invalid_envelopes_without_processing(self) -> None:
        async def processor(_event: CanonicalMessageEvent) -> dict[str, object]:
            raise AssertionError("ignored event must not be processed")

        bridge = SlackSocketModeBridge(converter=self.converter, processor=processor)
        ignored = bridge.dispatch(
            self.client,
            SocketModeRequest(
                type="interactive", envelope_id="ignored", payload={"type": "shortcut"}
            ),
        )
        invalid = bridge.dispatch(
            self.client,
            socket_request(
                {
                    "type": "event_callback",
                    "team_id": "T12345",
                    "event": {
                        "type": "message",
                        "channel": "C12345",
                        "user": "U12345",
                        "ts": "invalid",
                        "text": "ignored",
                    },
                }
            ),
        )

        self.assertEqual(ignored.status, "ignored")
        self.assertEqual(invalid.status, "invalid")
        self.assertEqual(invalid.error_code, "invalid_event")
        self.assertEqual(len(self.client.acknowledgements), 2)

    def test_processor_error_is_sanitized_after_acknowledgement(self) -> None:
        async def processor(_event: CanonicalMessageEvent) -> dict[str, object]:
            raise RuntimeError("private volunteer text must not escape")

        dispatch = SlackSocketModeBridge(converter=self.converter, processor=processor).dispatch(
            self.client, socket_request(self._payload())
        )

        self.assertEqual(dispatch.status, "processing_failed")
        self.assertEqual(dispatch.error_code, "processor_error")
        self.assertNotIn("private volunteer", dispatch.model_dump_json())
        self.assertEqual(self.client.order, ["ack"])


if __name__ == "__main__":
    unittest.main()
