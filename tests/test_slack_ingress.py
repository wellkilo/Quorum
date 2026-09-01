from __future__ import annotations

import hashlib
import hmac
import json
import unittest

from quorum.models import DataClassification
from quorum.slack_ingress import (
    SlackEventConverter,
    SlackIngressError,
    SlackSignatureVerifier,
)


class SlackSignatureVerifierTest(unittest.TestCase):
    def test_accepts_exact_slack_v0_signature_and_rejects_replay(self) -> None:
        secret = b"stage-four-signing-secret"
        body = b'{"type":"event_callback"}'
        timestamp = "1770000000"
        signature = (
            "v0="
            + hmac.new(
                secret, b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
            ).hexdigest()
        )
        verifier = SlackSignatureVerifier(secret, clock=lambda: 1770000000.0)

        verifier.verify(body, timestamp, signature)

        with self.assertRaisesRegex(SlackIngressError, "stale Slack request"):
            SlackSignatureVerifier(secret, clock=lambda: 1770000301.0).verify(
                body, timestamp, signature
            )

    def test_rejects_a_signature_for_different_bytes(self) -> None:
        verifier = SlackSignatureVerifier(b"stage-four-signing-secret", clock=lambda: 1.0)

        with self.assertRaisesRegex(SlackIngressError, "invalid Slack signature"):
            verifier.verify(b"actual", "1", "v0=not-the-signature")


class SlackEventConverterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.converter = SlackEventConverter(b"stage-four-pseudonym-key")

    def test_pseudonymizes_identifiers_and_redacts_pii_before_graph_input(self) -> None:
        payload = {
            "type": "event_callback",
            "team_id": "T12345",
            "event": {
                "type": "message",
                "channel": "C12345",
                "user": "U12345",
                "ts": "1770000000.000100",
                "text": (
                    "Ask <@U99999> at volunteer@example.org or +8613812345678; host 192.168.1.9."
                ),
            },
        }

        event = self.converter.to_canonical(payload)

        self.assertIsNotNone(event)
        assert event is not None
        self.assertIs(event.data_classification, DataClassification.REDACTED_REAL)
        self.assertNotIn("T12345", event.model_dump_json())
        self.assertNotIn("C12345", event.model_dump_json())
        self.assertNotIn("U12345", event.model_dump_json())
        self.assertNotIn("volunteer@example.org", event.text)
        self.assertNotIn("13812345678", event.text)
        self.assertNotIn("192.168.1.9", event.text)
        self.assertIn("<EMAIL_REDACTED>", event.text)
        self.assertIn("<PHONE_REDACTED>", event.text)
        self.assertIn("<IP_REDACTED>", event.text)

    def test_ignores_bot_and_non_message_events(self) -> None:
        bot = {
            "type": "event_callback",
            "team_id": "T12345",
            "event": {"type": "message", "subtype": "bot_message"},
        }

        self.assertIsNone(self.converter.to_canonical(bot))
        self.assertIsNone(self.converter.to_canonical({"type": "app_rate_limited"}))

    def test_fixed_synthetic_marker_can_preserve_its_content_classification(self) -> None:
        payload = {
            "type": "event_callback",
            "team_id": "T12345",
            "event": {
                "type": "message",
                "channel": "C12345",
                "user": "U12345",
                "ts": "1770000000.000100",
                "text": "Synthetic transport check: Quorum live evidence.",
            },
        }

        event = self.converter.to_canonical(
            payload, data_classification=DataClassification.SYNTHETIC
        )

        self.assertIsNotNone(event)
        self.assertIs(event.data_classification, DataClassification.SYNTHETIC)

    def test_malformed_message_timestamp_is_a_controlled_ingress_error(self) -> None:
        payload = {
            "type": "event_callback",
            "team_id": "T12345",
            "event": {
                "type": "message",
                "channel": "C12345",
                "user": "U12345",
                "ts": "not-a-timestamp",
                "text": "I will bring the keys.",
            },
        }

        with self.assertRaisesRegex(SlackIngressError, "timestamp"):
            self.converter.to_canonical(payload)

    def test_parses_url_verification_without_creating_an_event(self) -> None:
        body = json.dumps({"type": "url_verification", "challenge": "opaque"}).encode()
        payload = self.converter.parse(body)

        self.assertEqual(self.converter.challenge(payload), "opaque")
        self.assertIsNone(self.converter.to_canonical(payload))


if __name__ == "__main__":
    unittest.main()
