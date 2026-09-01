"""Collect PII-safe, zero-model evidence from a dedicated Slack test workspace."""

from __future__ import annotations

import argparse
import json
import os
import queue
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field
from slack_sdk.socket_mode import SocketModeClient

from quorum.models import CanonicalMessageEvent, DataClassification, StrictModel
from quorum.slack import SlackDeliveryError, SlackNotifier
from quorum.slack_ingress import SlackEventConverter
from quorum.slack_smoke import (
    SlackSurfaceSender,
    load_synthetic_snapshot,
    run_slack_surface_smoke,
)
from quorum.slack_socket import (
    SlackSocketModeBridge,
    build_slack_event_converter,
    build_slack_socket_client,
    validate_slack_manifest,
)

SYNTHETIC_TRANSPORT_MARKER = "Synthetic transport check: Quorum live evidence."
_COST_GATES = ("QUORUM_BEDROCK_ENABLED", "QUORUM_EXECUTION_ENABLED")


class SlackLiveEvidenceReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["provider_responses_validated"] = "provider_responses_validated"
    completed_at: datetime
    evidence_scope: Literal["dedicated_test_workspace_synthetic"] = (
        "dedicated_test_workspace_synthetic"
    )
    dataset_id: str
    transport: Literal["slack_socket_mode"] = "slack_socket_mode"
    envelope_ack_sent: Literal[True] = True
    canonical_events_observed: Literal[1] = 1
    canonical_event_classification: Literal[DataClassification.SYNTHETIC] = (
        DataClassification.SYNTHETIC
    )
    fixture_data_classification: Literal[DataClassification.SYNTHETIC] = (
        DataClassification.SYNTHETIC
    )
    messages_sent: Literal[3] = 3
    surface_types: tuple[
        Literal["group_receipt"],
        Literal["private_question"],
        Literal["weekly_summary"],
    ] = ("group_receipt", "private_question", "weekly_summary")
    web_api_responses_validated: Literal[4] = 4
    visual_inspection_required: Literal[True] = True
    model_calls: Literal[0] = 0
    memory_events: Literal[0] = 0
    database_writes: Literal[0] = 0
    gateway_tool_calls: Literal[0] = 0
    google_workspace_calls: Literal[0] = 0
    execution_tool_calls: Literal[0] = 0
    external_side_effect_calls: int = Field(default=4, ge=4, le=4)


class SlackLiveEvidenceError(RuntimeError):
    """Sanitized live-evidence failure that cannot include Slack response content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def require_closed_cost_gates(environment: Mapping[str, str] | None = None) -> None:
    values = environment if environment is not None else os.environ
    for name in _COST_GATES:
        value = values.get(name, "").strip().lower()
        if value not in {"", "0", "false"}:
            raise RuntimeError(f"{name} must remain false for Slack live evidence")


def run_slack_live_evidence(
    *,
    client: SocketModeClient,
    converter: SlackEventConverter,
    sender: SlackSurfaceSender,
    channel_id: str,
    participant_id: str,
    timeout_seconds: float,
    now: datetime | None = None,
) -> SlackLiveEvidenceReport:
    """Wait for the fixed marker, then post the three synthetic product surfaces."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    observed: queue.Queue[CanonicalMessageEvent] = queue.Queue(maxsize=1)
    expected_channel_id = converter.canonical_channel_id(channel_id)

    async def capture_marker(event: CanonicalMessageEvent) -> dict[str, object]:
        marker_matched = (
            event.channel_id == expected_channel_id and event.text == SYNTHETIC_TRANSPORT_MARKER
        )
        if marker_matched and observed.empty():
            observed.put_nowait(event)
        return {"marker_matched": marker_matched}

    bridge = SlackSocketModeBridge(
        converter=converter,
        processor=capture_marker,
        data_classification=DataClassification.SYNTHETIC,
    )
    try:
        client.socket_mode_request_listeners.append(bridge)
        client.connect()
        event = observed.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        raise SlackLiveEvidenceError("marker_timeout") from exc
    except Exception as exc:
        raise SlackLiveEvidenceError("socket_connection_failed") from exc
    finally:
        with suppress(Exception):
            client.disconnect()

    if event.data_classification is not DataClassification.SYNTHETIC:
        raise SlackLiveEvidenceError("invalid_marker_classification")
    snapshot = load_synthetic_snapshot()
    result = run_slack_surface_smoke(
        sender,
        channel_id=channel_id,
        participant_id=participant_id,
        snapshot=snapshot,
        now=now,
    )
    if result.messages_sent != 3 or result.data_classification is not DataClassification.SYNTHETIC:
        raise SlackLiveEvidenceError("invalid_surface_result")
    return SlackLiveEvidenceReport(
        completed_at=(now or datetime.now(UTC)).astimezone(UTC),
        dataset_id=result.dataset_id,
    )


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def write_evidence_report(path: Path, serialized: str) -> None:
    """Create a report without ever overwriting prior evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            output.write(serialized + "\n")
    except FileExistsError as exc:
        raise RuntimeError("--output must not overwrite an existing file") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect a zero-model Slack transport and three-surface evidence report."
    )
    parser.add_argument("--confirm-live-posts", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the PII-safe JSON report to a new file after successful provider responses.",
    )
    args = parser.parse_args(argv)
    validate_slack_manifest()

    if not args.confirm_live_posts:
        print(
            json.dumps(
                {
                    "status": "preview",
                    "expected_marker": SYNTHETIC_TRANSPORT_MARKER,
                    "messages_to_send_after_marker": 3,
                    "visual_inspection_required": True,
                    "model_calls": 0,
                    "gateway_tool_calls": 0,
                    "google_workspace_calls": 0,
                },
                sort_keys=True,
            )
        )
        return 0

    try:
        require_closed_cost_gates()
        if args.timeout_seconds <= 0:
            raise RuntimeError("--timeout-seconds must be positive")
        channel_id = _required_environment("QUORUM_SLACK_DEMO_CHANNEL_ID")
        participant_id = _required_environment("QUORUM_SLACK_DEMO_PARTICIPANT_ID")
        client = build_slack_socket_client()
        report = run_slack_live_evidence(
            client=client,
            converter=build_slack_event_converter(),
            sender=SlackNotifier(client.web_client),
            channel_id=channel_id,
            participant_id=participant_id,
            timeout_seconds=args.timeout_seconds,
        )
    except SlackDeliveryError as exc:
        parser.error(f"Slack delivery failed: {exc.code}")
    except SlackLiveEvidenceError as exc:
        parser.error(f"Slack evidence failed: {exc.code}")
    except RuntimeError as exc:
        parser.error(str(exc))
    serialized = report.model_dump_json(indent=2)
    if args.output is not None:
        try:
            write_evidence_report(args.output, serialized)
        except RuntimeError as exc:
            parser.error(str(exc))
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
