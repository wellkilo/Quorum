from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest

from starlette.testclient import TestClient

from quorum.runtime import RuntimeInvocation, create_app

SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
VALID_SESSION_ID = "session_00000000000000000000000001"


def invocation_payload() -> dict[str, object]:
    return {
        "organization_id": "org_opaque",
        "prompt": "Create the already-approved tentative event.",
        "data_classification": "synthetic",
        "action_request": {
            "action_id": "action_opaque",
            "organization_id": "org_opaque",
            "requested_by_id": "person_requester",
            "action_class": "event_decision",
            "tool_name": "calendar_create_tentative_event",
            "summary": "Create a tentative planning event",
            "reversibility": "reversible",
            "impact_radius": "individual",
            "money_impact": "none",
            "candidate_decider_ids": ["person_decider"],
            "action_arguments": {},
            "requested_at": "2026-08-26T10:00:00Z",
        },
    }


class FakeRuntimeInvoker:
    def __init__(self) -> None:
        self.calls: list[tuple[RuntimeInvocation, str]] = []

    async def __call__(self, payload: RuntimeInvocation, session_id: str) -> dict[str, object]:
        self.calls.append((payload, session_id))
        return {"session_id": session_id, "status": "completed"}


class FakeSlackProcessor:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def __call__(self, event: object) -> dict[str, object]:
        self.events.append(event)
        return {"accepted": True}


class FakeUndoExecutor:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def undo(self, token: str) -> dict[str, str]:
        self.tokens.append(token)
        return {"status": "undone"}


class RuntimeHttpContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.invoker = FakeRuntimeInvoker()
        self.processor = FakeSlackProcessor()
        self.undoer = FakeUndoExecutor()
        self.signing_secret = b"stage-four-signing-secret"
        self.app = create_app(
            runtime_invoker=self.invoker,
            slack_processor=self.processor,
            undo_executor=self.undoer,
            slack_signing_secret=self.signing_secret,
            slack_pseudonym_key=b"stage-four-pseudonym-key",
        )
        self.client = TestClient(self.app)

    def test_invocations_requires_runtime_session_and_tenant_match(self) -> None:
        missing = self.client.post("/invocations", json=invocation_payload())
        mismatched = invocation_payload()
        mismatched["organization_id"] = "org_other"
        invalid_tenant = self.client.post(
            "/invocations",
            json=mismatched,
            headers={SESSION_HEADER: VALID_SESSION_ID},
        )

        self.assertEqual(missing.status_code, 503)
        self.assertEqual(invalid_tenant.status_code, 422)
        self.assertEqual(self.invoker.calls, [])

    def test_invalid_runtime_session_id_is_rejected_before_memory_use(self) -> None:
        response = self.client.post(
            "/invocations",
            json=invocation_payload(),
            headers={SESSION_HEADER: "bad/session"},
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("session ID", response.json()["error"])
        self.assertEqual(self.invoker.calls, [])

    def test_valid_runtime_invocation_receives_the_exact_session_id(self) -> None:
        response = self.client.post(
            "/invocations",
            json=invocation_payload(),
            headers={SESSION_HEADER: VALID_SESSION_ID},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session_id"], VALID_SESSION_ID)
        self.assertEqual(len(self.invoker.calls), 1)
        self.assertEqual(self.invoker.calls[0][0].organization_id, "org_opaque")

    def test_slack_url_verification_and_signed_message_ack(self) -> None:
        timestamp = str(int(time.time()))
        challenge_body = json.dumps(
            {"type": "url_verification", "challenge": "opaque-challenge"}
        ).encode()
        challenge_headers = self._slack_headers(challenge_body, timestamp)
        message_body = json.dumps(
            {
                "type": "event_callback",
                "team_id": "T12345",
                "event": {
                    "type": "message",
                    "channel": "C12345",
                    "user": "U12345",
                    "ts": f"{timestamp}.000100",
                    "text": "I will bring the keys.",
                },
            }
        ).encode()
        message_headers = self._slack_headers(message_body, timestamp)

        challenge = self.client.post(
            "/integrations/slack/events", content=challenge_body, headers=challenge_headers
        )
        accepted = self.client.post(
            "/integrations/slack/events", content=message_body, headers=message_headers
        )

        self.assertEqual(challenge.json(), {"challenge": "opaque-challenge"})
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.json()["accepted"])
        self.assertEqual(len(self.processor.events), 1)

    def test_undo_get_only_confirms_and_post_executes_once(self) -> None:
        confirmation = self.client.get("/actions/undo?token=signed-token")

        self.assertEqual(confirmation.status_code, 200)
        self.assertIn("Undo this Quorum action?", confirmation.text)
        self.assertEqual(confirmation.headers["cache-control"], "no-store")
        self.assertEqual(confirmation.headers["referrer-policy"], "no-referrer")
        self.assertEqual(self.undoer.tokens, [])

        execution = self.client.post("/actions/undo", data={"token": "signed-token"})

        self.assertEqual(execution.status_code, 200)
        self.assertEqual(execution.json(), {"status": "undone"})
        self.assertEqual(self.undoer.tokens, ["signed-token"])

    def test_public_replay_endpoint_keeps_synthetic_provenance(self) -> None:
        home = self.client.get("/")
        favicon = self.client.get("/favicon.svg")
        replay = self.client.post("/demo/replays/synthetic-week")
        replay_id = replay.json()["replay_id"]
        metrics = self.client.get(f"/demo/metrics/{replay_id}")

        self.assertEqual(home.status_code, 200)
        self.assertIn("synthetic data only", home.text)
        self.assertIn("No live AgentCore backend", home.text)
        self.assertEqual(favicon.status_code, 200)
        self.assertEqual(favicon.headers["content-type"], "image/svg+xml")
        self.assertIn("<title", favicon.text)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(metrics.status_code, 200)
        self.assertEqual(metrics.json()["data_classification"], "synthetic")
        self.assertIn("not a measured real-world outcome", metrics.json()["disclaimer"])

    def _slack_headers(self, body: bytes, timestamp: str) -> dict[str, str]:
        signature = (
            "v0="
            + hmac.new(
                self.signing_secret,
                b"v0:" + timestamp.encode() + b":" + body,
                hashlib.sha256,
            ).hexdigest()
        )
        return {
            "content-type": "application/json",
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": signature,
        }


if __name__ == "__main__":
    unittest.main()
