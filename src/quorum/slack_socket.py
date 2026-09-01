"""Slack Socket Mode bridge for Quorum's existing, PII-safe event boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import queue
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from threading import Event
from typing import Literal, Protocol

from slack_sdk import WebClient
from slack_sdk.socket_mode import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse

from quorum.models import CanonicalMessageEvent, DataClassification, StrictModel
from quorum.slack_ingress import SlackEventConverter, SlackIngressError

DEFAULT_MANIFEST = Path(__file__).with_name("slack-app-manifest.json")
REQUIRED_BOT_SCOPES = frozenset({"channels:history", "chat:write", "im:write"})
REQUIRED_BOT_EVENTS = frozenset({"message.channels"})


SlackEventProcessor = Callable[
    [CanonicalMessageEvent], Coroutine[object, object, dict[str, object]]
]


class SocketResponseSender(Protocol):
    def send_socket_mode_response(self, response: SocketModeResponse) -> None: ...


class SlackSocketDispatch(StrictModel):
    status: Literal["accepted", "ignored", "invalid", "processing_failed"]
    event_id: str | None = None
    data_classification: DataClassification | None = None
    error_code: str | None = None


class SlackSocketProbeReport(StrictModel):
    status: Literal["accepted"]
    data_classification: DataClassification
    event_id: str
    events_observed: int = 1
    model_calls: int = 0
    memory_events: int = 0
    database_writes: int = 0
    gateway_tool_calls: int = 0
    external_side_effect_calls: int = 0


class SlackManifestMetadata(StrictModel):
    major_version: Literal[1]
    minor_version: Literal[1]


class SlackDisplayInformation(StrictModel):
    name: Literal["Quorum"]
    description: Literal["Coordinate more. Interrupt less."]
    background_color: Literal["#07131f"]


class SlackBotUser(StrictModel):
    display_name: Literal["Quorum"]
    always_online: Literal[False]


class SlackFeatures(StrictModel):
    bot_user: SlackBotUser


class SlackBotScopes(StrictModel):
    bot: list[Literal["channels:history", "chat:write", "im:write"]]


class SlackOauthConfig(StrictModel):
    scopes: SlackBotScopes


class SlackEventSubscriptions(StrictModel):
    bot_events: list[Literal["message.channels"]]


class SlackManifestSettings(StrictModel):
    event_subscriptions: SlackEventSubscriptions
    org_deploy_enabled: Literal[False]
    socket_mode_enabled: Literal[True]
    token_rotation_enabled: Literal[False]


class SlackAppManifest(StrictModel):
    metadata: SlackManifestMetadata
    display_information: SlackDisplayInformation
    features: SlackFeatures
    oauth_config: SlackOauthConfig
    settings: SlackManifestSettings


class SlackSocketModeBridge:
    """Acknowledge first, then convert and process only canonical message events."""

    def __init__(
        self,
        *,
        converter: SlackEventConverter,
        processor: SlackEventProcessor,
        result_sink: Callable[[SlackSocketDispatch], None] | None = None,
    ) -> None:
        self._converter = converter
        self._processor = processor
        self._result_sink = result_sink

    def __call__(self, client: SocketResponseSender, request: SocketModeRequest) -> None:
        result = self.dispatch(client, request)
        if self._result_sink is not None:
            self._result_sink(result)

    def dispatch(
        self, client: SocketResponseSender, request: SocketModeRequest
    ) -> SlackSocketDispatch:
        client.send_socket_mode_response(SocketModeResponse(envelope_id=request.envelope_id))
        if request.type != "events_api":
            return SlackSocketDispatch(status="ignored")
        try:
            event = self._converter.to_canonical(request.payload)
        except SlackIngressError:
            return SlackSocketDispatch(status="invalid", error_code="invalid_event")
        if event is None:
            return SlackSocketDispatch(status="ignored")
        try:
            asyncio.run(self._processor(event))
        except Exception:
            return SlackSocketDispatch(
                status="processing_failed",
                event_id=event.message_id,
                data_classification=event.data_classification,
                error_code="processor_error",
            )
        return SlackSocketDispatch(
            status="accepted",
            event_id=event.message_id,
            data_classification=event.data_classification,
        )


def validate_slack_manifest(path: Path = DEFAULT_MANIFEST) -> SlackAppManifest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Slack manifest must be a JSON object")
    metadata = raw.pop("_metadata", None)
    raw["metadata"] = metadata
    manifest = SlackAppManifest.model_validate(raw)
    if set(manifest.oauth_config.scopes.bot) != REQUIRED_BOT_SCOPES:
        raise ValueError("Slack manifest bot scopes must match the reviewed minimum set")
    if set(manifest.settings.event_subscriptions.bot_events) != REQUIRED_BOT_EVENTS:
        raise ValueError("Slack manifest bot events must match the reviewed message subscription")
    return manifest


def _required_secret(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _client() -> SocketModeClient:
    return SocketModeClient(
        app_token=_required_secret("QUORUM_SLACK_APP_TOKEN"),
        web_client=WebClient(token=_required_secret("QUORUM_SLACK_BOT_TOKEN")),
    )


def _converter() -> SlackEventConverter:
    return SlackEventConverter(_required_secret("QUORUM_SLACK_PSEUDONYM_KEY").encode())


def _validate_command(manifest: Path) -> int:
    validated = validate_slack_manifest(manifest)
    print(
        json.dumps(
            {
                "status": "valid",
                "transport": "socket_mode",
                "bot_events": validated.settings.event_subscriptions.bot_events,
                "bot_scopes": validated.oauth_config.scopes.bot,
                "model_calls": 0,
                "external_side_effect_calls": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def _probe_command(timeout_seconds: float) -> int:
    reports: queue.Queue[SlackSocketProbeReport] = queue.Queue(maxsize=1)

    async def observe(_event: CanonicalMessageEvent) -> dict[str, object]:
        return {"observed": True}

    def capture(dispatch: SlackSocketDispatch) -> None:
        if (
            dispatch.status == "accepted"
            and dispatch.event_id is not None
            and dispatch.data_classification is not None
            and reports.empty()
        ):
            reports.put_nowait(
                SlackSocketProbeReport(
                    status="accepted",
                    event_id=dispatch.event_id,
                    data_classification=dispatch.data_classification,
                )
            )

    client = _client()
    client.socket_mode_request_listeners.append(
        SlackSocketModeBridge(converter=_converter(), processor=observe, result_sink=capture)
    )
    client.connect()
    try:
        report = reports.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise RuntimeError("no Slack message event arrived before the probe timeout") from exc
    finally:
        client.disconnect()
    print(report.model_dump_json())
    return 0


def _serve_command() -> int:
    from quorum.database import DatabaseSettings, create_database_engine
    from quorum.orchestration import BedrockSettings
    from quorum.runtime import ProductionSlackProcessor

    BedrockSettings.from_environment()
    processor = ProductionSlackProcessor(
        create_database_engine(DatabaseSettings.from_environment())
    )

    def report(dispatch: SlackSocketDispatch) -> None:
        print(dispatch.model_dump_json(exclude_none=True), flush=True)

    client = _client()
    client.socket_mode_request_listeners.append(
        SlackSocketModeBridge(converter=_converter(), processor=processor, result_sink=report)
    )
    client.connect()
    try:
        Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="Validate the reviewed manifest without network calls.")
    probe = subparsers.add_parser("probe", help="Observe one event without model or persistence.")
    probe.add_argument("--timeout-seconds", type=float, default=120.0)
    subparsers.add_parser(
        "serve", help="Run the production processor; the Bedrock gate must be open."
    )
    args = parser.parse_args(argv)
    validate_slack_manifest(args.manifest)
    if args.command == "validate":
        return _validate_command(args.manifest)
    if args.command == "probe":
        if args.timeout_seconds <= 0:
            parser.error("--timeout-seconds must be positive")
        return _probe_command(args.timeout_seconds)
    return _serve_command()


if __name__ == "__main__":
    raise SystemExit(main())
