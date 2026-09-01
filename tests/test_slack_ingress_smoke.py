from __future__ import annotations

import hashlib
import hmac
import unittest
from typing import Any

from quorum.slack_ingress_smoke import run_slack_ingress_smoke


class FakeIngressTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, dict[str, str], float]] = []

    def post(
        self, url: str, body: bytes, headers: dict[str, str], timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append((url, body, headers, timeout_seconds))
        if len(self.calls) == 1:
            return 200, {"challenge": "quorum-local-verification"}
        if len(self.calls) == 2:
            return 401, {"error": "invalid Slack signature"}
        return 503, {"error": "Bedrock model calls are disabled"}


class SlackIngressSmokeTest(unittest.TestCase):
    def test_real_signature_smoke_checks_challenge_forgery_and_cost_gate(self) -> None:
        secret = b"slack-http-smoke-secret"
        transport = FakeIngressTransport()

        report = run_slack_ingress_smoke(
            base_url="http://127.0.0.1:8080",
            signing_secret=secret,
            transport=transport,
            now=1770000000,
        )

        self.assertEqual(report.challenge_status, 200)
        self.assertEqual(report.forged_signature_status, 401)
        self.assertEqual(report.cost_gated_event_status, 503)
        self.assertEqual(report.model_calls, 0)
        self.assertEqual(report.database_writes, 0)
        self.assertEqual(report.external_side_effect_calls, 0)
        self.assertEqual(len(transport.calls), 3)
        for url, body, headers, timeout in (transport.calls[0], transport.calls[2]):
            expected = (
                "v0=" + hmac.new(secret, b"v0:1770000000:" + body, hashlib.sha256).hexdigest()
            )
            self.assertEqual(url, "http://127.0.0.1:8080/integrations/slack/events")
            self.assertEqual(headers["X-Slack-Signature"], expected)
            self.assertEqual(timeout, 10.0)
        self.assertEqual(transport.calls[1][2]["X-Slack-Signature"], "v0=" + "0" * 64)

    def test_smoke_rejects_a_server_that_accepts_the_forged_signature(self) -> None:
        class UnsafeTransport(FakeIngressTransport):
            def post(
                self,
                url: str,
                body: bytes,
                headers: dict[str, str],
                timeout_seconds: float,
            ) -> tuple[int, dict[str, Any]]:
                status, payload = super().post(url, body, headers, timeout_seconds)
                return (200, payload) if len(self.calls) == 2 else (status, payload)

        with self.assertRaisesRegex(RuntimeError, "accepted a forged signature"):
            run_slack_ingress_smoke(
                base_url="https://quorum.example",
                signing_secret=b"slack-http-smoke-secret",
                transport=UnsafeTransport(),
                now=1770000000,
            )


if __name__ == "__main__":
    unittest.main()
