"""Slack Events verification and conversion into Quorum's canonical boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from quorum.models import CanonicalMessageEvent, DataClassification, MessageSource

SLACK_REPLAY_WINDOW_SECONDS = 300
_SLACK_MENTION = re.compile(r"<@([UW][A-Z0-9]{2,})>")
_SLACK_MESSAGE_TIMESTAMP = re.compile(r"^[0-9]{10,20}\.[0-9]{6}$")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)
_PHONE = re.compile(
    r"(?<![\d])(?:\+?86[- ]?)?1[3-9]\d(?:[- ]?\d){8}(?![\d])"
    r"|(?<![\d])\+[1-9]\d{0,2}(?:[- .]?\d){7,12}(?![\d])"
)
_IPV4 = re.compile(
    r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d])"
)


class SlackIngressError(ValueError):
    pass


class SlackSignatureVerifier:
    def __init__(
        self,
        signing_secret: bytes,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if len(signing_secret) < 16:
            raise ValueError("Slack signing secret must be at least 16 bytes")
        self._secret = signing_secret
        self._clock = clock or time.time

    def verify(self, body: bytes, timestamp: str | None, signature: str | None) -> None:
        if timestamp is None or signature is None:
            raise SlackIngressError("missing Slack signature headers")
        try:
            request_time = int(timestamp)
        except ValueError as exc:
            raise SlackIngressError("invalid Slack request timestamp") from exc
        if abs(self._clock() - request_time) > SLACK_REPLAY_WINDOW_SECONDS:
            raise SlackIngressError("stale Slack request")
        base = b"v0:" + timestamp.encode("ascii") + b":" + body
        expected = "v0=" + hmac.new(self._secret, base, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise SlackIngressError("invalid Slack signature")


class SlackEventConverter:
    def __init__(self, pseudonym_key: bytes) -> None:
        if len(pseudonym_key) < 16:
            raise ValueError("Slack pseudonym key must be at least 16 bytes")
        self._key = pseudonym_key

    def parse(self, body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SlackIngressError("invalid Slack JSON") from exc
        if not isinstance(payload, dict):
            raise SlackIngressError("Slack payload must be an object")
        return payload

    def challenge(self, payload: dict[str, Any]) -> str | None:
        challenge = payload.get("challenge")
        return (
            challenge
            if payload.get("type") == "url_verification" and isinstance(challenge, str)
            else None
        )

    def to_canonical(self, payload: dict[str, Any]) -> CanonicalMessageEvent | None:
        if payload.get("type") != "event_callback":
            return None
        event = payload.get("event")
        if not isinstance(event, dict) or event.get("type") != "message":
            return None
        if event.get("subtype") is not None or event.get("bot_id") is not None:
            return None
        team_id = _required_string(payload, "team_id")
        channel_id = _required_string(event, "channel")
        user_id = _required_string(event, "user")
        message_ts = _required_string(event, "ts")
        if _SLACK_MESSAGE_TIMESTAMP.fullmatch(message_ts) is None:
            raise SlackIngressError("invalid Slack message timestamp")
        try:
            occurred_at = datetime.fromtimestamp(float(message_ts), tz=UTC)
        except (OSError, OverflowError, ValueError) as exc:
            raise SlackIngressError("invalid Slack message timestamp") from exc
        text = self._redact_text(_required_string(event, "text"))
        source_ref = f"slack:{self._opaque('channel', channel_id)}:{message_ts}"
        return CanonicalMessageEvent(
            organization_id=self._opaque("organization", team_id),
            channel_id=self._opaque("channel", channel_id),
            message_id=self._opaque("message", f"{channel_id}:{message_ts}"),
            actor_id=self._opaque("person", user_id),
            occurred_at=occurred_at,
            text=text,
            data_classification=DataClassification.REDACTED_REAL,
            source=MessageSource(
                provider="slack",
                workspace_id=self._opaque("workspace", team_id),
                source_message_ref=source_ref,
            ),
        )

    def _opaque(self, kind: str, value: str) -> str:
        digest = hmac.new(self._key, f"{kind}\0{value}".encode(), hashlib.sha256).hexdigest()[:24]
        return f"{kind}_{digest}"

    def _redact_text(self, value: str) -> str:
        value = _SLACK_MENTION.sub(
            lambda match: f"<@{self._opaque('person', match.group(1))}>", value
        )
        value = _EMAIL.sub("<EMAIL_REDACTED>", value)
        value = _PHONE.sub("<PHONE_REDACTED>", value)
        return _IPV4.sub("<IP_REDACTED>", value)


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise SlackIngressError(f"Slack field is required: {key}")
    return item
