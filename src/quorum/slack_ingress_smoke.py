"""Exercise Quorum's HTTP Slack ingress with real v0 request signatures."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from collections.abc import Sequence
from http.client import HTTPConnection, HTTPSConnection
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from quorum.models import StrictModel


class JsonTransport(Protocol):
    def post(
        self, url: str, body: bytes, headers: dict[str, str], timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]: ...


class HttpJsonTransport:
    def post(
        self, url: str, body: bytes, headers: dict[str, str], timeout_seconds: float
    ) -> tuple[int, dict[str, Any]]:
        target = urlsplit(url)
        if target.scheme not in {"http", "https"} or not target.hostname:
            raise ValueError("Slack smoke base URL must be HTTP or HTTPS")
        connection_type = HTTPSConnection if target.scheme == "https" else HTTPConnection
        connection = connection_type(target.hostname, target.port, timeout=timeout_seconds)
        path = target.path or "/"
        if target.query:
            path = f"{path}?{target.query}"
        try:
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read()
        finally:
            connection.close()
        try:
            payload = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Slack ingress returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Slack ingress returned a non-object JSON response")
        return response.status, payload


class SlackIngressSmokeReport(StrictModel):
    challenge_status: int
    forged_signature_status: int
    cost_gated_event_status: int
    signature_algorithm: str = "hmac-sha256-v0"
    model_calls: int = 0
    memory_events: int = 0
    database_writes: int = 0
    gateway_tool_calls: int = 0
    external_side_effect_calls: int = 0


def _signature(secret: bytes, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    return "v0=" + hmac.new(secret, base, hashlib.sha256).hexdigest()


def _headers(secret: bytes, timestamp: str, body: bytes) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": _signature(secret, timestamp, body),
    }


def run_slack_ingress_smoke(
    *,
    base_url: str,
    signing_secret: bytes,
    transport: JsonTransport,
    now: int | None = None,
    timeout_seconds: float = 10.0,
) -> SlackIngressSmokeReport:
    timestamp = str(now or int(time.time()))
    endpoint = urljoin(base_url.rstrip("/") + "/", "integrations/slack/events")
    challenge_value = "quorum-local-verification"
    challenge_body = json.dumps(
        {"type": "url_verification", "challenge": challenge_value},
        separators=(",", ":"),
    ).encode()
    challenge_status, challenge = transport.post(
        endpoint,
        challenge_body,
        _headers(signing_secret, timestamp, challenge_body),
        timeout_seconds,
    )
    if challenge_status != 200 or challenge.get("challenge") != challenge_value:
        raise RuntimeError("Slack URL verification handshake failed")

    forged_headers = _headers(signing_secret, timestamp, challenge_body)
    forged_headers["X-Slack-Signature"] = "v0=" + "0" * 64
    forged_status, _ = transport.post(endpoint, challenge_body, forged_headers, timeout_seconds)
    if forged_status != 401:
        raise RuntimeError("Slack ingress accepted a forged signature")

    event_body = json.dumps(
        {
            "type": "event_callback",
            "team_id": "TQUORUMTEST",
            "event_id": "EvQUORUMTEST",
            "event_time": int(timestamp),
            "event": {
                "type": "message",
                "channel": "CQUORUMTEST",
                "user": "UQUORUMTEST",
                "ts": f"{timestamp}.000100",
                "text": "Synthetic ingress check: I will bring the keys on Friday.",
            },
        },
        separators=(",", ":"),
    ).encode()
    event_status, event_response = transport.post(
        endpoint,
        event_body,
        _headers(signing_secret, timestamp, event_body),
        timeout_seconds,
    )
    if event_status != 503 or "Bedrock model calls are disabled" not in str(
        event_response.get("error", "")
    ):
        raise RuntimeError("Slack ingress did not fail closed at the disabled model gate")

    return SlackIngressSmokeReport(
        challenge_status=challenge_status,
        forged_signature_status=forged_status,
        cost_gated_event_status=event_status,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    secret = os.environ.get("QUORUM_SLACK_SIGNING_SECRET", "").encode()
    if len(secret) < 16:
        parser.error("QUORUM_SLACK_SIGNING_SECRET must contain at least 16 bytes")
    report = run_slack_ingress_smoke(
        base_url=args.base_url,
        signing_secret=secret,
        transport=HttpJsonTransport(),
        timeout_seconds=args.timeout_seconds,
    )
    print(report.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
