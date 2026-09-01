#!/usr/bin/env python3
"""Prepare, verify, and restore a cost-bounded AgentCore trace evidence run."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import boto3

POLICY_PREFIX = "QuorumTransactionSearchVerification-"
MANAGED_LOG_GROUPS = ("aws/spans", "/aws/application-signals/data")
PROBE_SPAN_NAME = "quorum.observability.probe"
PRIVACY_SENTINEL = "synthetic-payload-must-not-appear-in-telemetry"
ALLOWED_QUORUM_ATTRIBUTES = {
    "quorum.data_classification",
    "quorum.organization_id",
    "quorum.probe_id",
    "quorum.session_id",
}
FORBIDDEN_MARKERS = (
    PRIVACY_SENTINEL,
    "InvokeModel",
    "CreateEvent",
    "InvokeGateway",
    "tools/call",
    "slack.com",
    "googleapis.com",
)


def _write_state(path: Path, state: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _transaction_policy(account_id: str, region: str) -> str:
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "QuorumTransactionSearchXRayAccess",
                "Effect": "Allow",
                "Principal": {"Service": "xray.amazonaws.com"},
                "Action": "logs:PutLogEvents",
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:aws/spans:*",
                    f"arn:aws:logs:{region}:{account_id}:log-group:/aws/application-signals/data:*",
                ],
                "Condition": {
                    "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{region}:{account_id}:*"},
                    "StringEquals": {"aws:SourceAccount": account_id},
                },
            }
        ],
    }
    return json.dumps(policy, separators=(",", ":"), sort_keys=True)


def _log_group_exists(logs: Any, name: str) -> bool:
    groups = logs.describe_log_groups(logGroupNamePrefix=name).get("logGroups", [])
    return any(item.get("logGroupName") == name for item in groups)


def prepare(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"QuorumTransactionSearchVerification-[A-Za-z0-9-]+", args.policy_name):
        raise ValueError("policy name must be unique and use the Quorum verification prefix")
    session = boto3.Session(region_name=args.region)
    logs = session.client("logs")
    xray = session.client("xray")
    existing = logs.describe_resource_policies(policyScope="ACCOUNT").get("resourcePolicies", [])
    if any(item.get("policyName") == args.policy_name for item in existing):
        raise RuntimeError("refusing to overwrite an existing CloudWatch Logs resource policy")

    destination = xray.get_trace_segment_destination().get("Destination", "XRay")
    indexing_rules = xray.get_indexing_rules().get("IndexingRules", [])
    default_rule = next(item for item in indexing_rules if item.get("Name") == "Default")
    percentage = default_rule["Rule"]["Probabilistic"]["DesiredSamplingPercentage"]
    state: dict[str, object] = {
        "policy_name": args.policy_name,
        "policy_created": False,
        "previous_destination": destination,
        "previous_indexing_percentage": percentage,
        "managed_log_groups_preexisting": {
            name: _log_group_exists(logs, name) for name in MANAGED_LOG_GROUPS
        },
        "region": args.region,
    }
    _write_state(args.state_file, state)

    logs.put_resource_policy(
        policyName=args.policy_name,
        policyDocument=_transaction_policy(args.account_id, args.region),
    )
    state["policy_created"] = True
    _write_state(args.state_file, state)
    xray.update_trace_segment_destination(Destination="CloudWatchLogs")
    xray.update_indexing_rule(
        Name="Default", Rule={"Probabilistic": {"DesiredSamplingPercentage": 0.0}}
    )

    for _attempt in range(30):
        current = xray.get_trace_segment_destination()
        if current.get("Destination") == "CloudWatchLogs" and current.get("Status") == "ACTIVE":
            print("transaction_search_destination=CloudWatchLogs")
            print("transaction_search_indexing_percentage=0.0")
            print(f"transaction_search_policy={args.policy_name}")
            return
        time.sleep(2)
    raise RuntimeError("Transaction Search destination did not become active")


def restore(args: argparse.Namespace) -> None:
    if not args.state_file.is_file():
        print("observability_state=absent")
        return
    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    session = boto3.Session(region_name=state["region"])
    logs = session.client("logs")
    xray = session.client("xray")
    errors: list[str] = []
    current_destination = xray.get_trace_segment_destination()
    current_rules = xray.get_indexing_rules().get("IndexingRules", [])
    current_default = next(item for item in current_rules if item.get("Name") == "Default")
    current_percentage = current_default["Rule"]["Probabilistic"]["DesiredSamplingPercentage"]
    try:
        if current_percentage != state["previous_indexing_percentage"]:
            xray.update_indexing_rule(
                Name="Default",
                Rule={
                    "Probabilistic": {
                        "DesiredSamplingPercentage": state["previous_indexing_percentage"]
                    }
                },
            )
    except Exception as exc:  # cleanup must continue across independent resources
        errors.append(f"indexing restore failed: {type(exc).__name__}")
    try:
        if current_destination.get("Destination") != state["previous_destination"]:
            xray.update_trace_segment_destination(Destination=state["previous_destination"])
    except Exception as exc:  # cleanup must continue across independent resources
        errors.append(f"destination restore failed: {type(exc).__name__}")
    if state.get("policy_created"):
        try:
            logs.delete_resource_policy(policyName=state["policy_name"])
        except Exception as exc:  # cleanup must report, not hide, incomplete rollback
            errors.append(f"policy deletion failed: {type(exc).__name__}")
    if errors:
        raise RuntimeError("; ".join(errors))
    for _attempt in range(30):
        restored_destination = xray.get_trace_segment_destination()
        if (
            restored_destination.get("Destination") == state["previous_destination"]
            and restored_destination.get("Status") == "ACTIVE"
        ):
            break
        time.sleep(2)
    else:
        raise RuntimeError("trace destination restoration did not become active")
    current_rules = xray.get_indexing_rules().get("IndexingRules", [])
    current_default = next(item for item in current_rules if item.get("Name") == "Default")
    current_percentage = current_default["Rule"]["Probabilistic"]["DesiredSamplingPercentage"]
    if current_percentage != state["previous_indexing_percentage"]:
        raise RuntimeError("indexing percentage restoration could not be verified")
    remaining_policies = logs.describe_resource_policies(policyScope="ACCOUNT").get(
        "resourcePolicies", []
    )
    if any(item.get("policyName") == state["policy_name"] for item in remaining_policies):
        raise RuntimeError("temporary Transaction Search policy still exists after cleanup")
    preexisting = state.get("managed_log_groups_preexisting", {})
    for group_name in MANAGED_LOG_GROUPS:
        if isinstance(preexisting, Mapping) and not preexisting.get(group_name, True):
            if _log_group_exists(logs, group_name):
                logs.delete_log_group(logGroupName=group_name)
            if _log_group_exists(logs, group_name):
                raise RuntimeError(
                    f"temporary managed log group still exists after cleanup: {group_name}"
                )
    print(f"transaction_search_destination_restored={state['previous_destination']}")
    print(f"transaction_search_indexing_restored={state['previous_indexing_percentage']}")
    print("transaction_search_policy_removed=true")
    print(
        "temporary_managed_log_groups_removed="
        f"{
            sum(
                1
                for name in MANAGED_LOG_GROUPS
                if isinstance(preexisting, Mapping) and not preexisting.get(name, True)
            )
        }"
    )


def _walk(value: object) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str) and value.startswith(("{", "[")):
        try:
            yield from _walk(json.loads(value))
        except json.JSONDecodeError:
            return


def _matching_spans(messages: Iterable[str], trace_id: str) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    for message in messages:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        for item in _walk(payload):
            if item.get("traceId") == trace_id:
                matches.append(item)
    return matches


def verify(args: argparse.Namespace) -> None:
    logs = boto3.Session(region_name=args.region).client("logs")
    group_name = f"/aws/bedrock-agentcore/runtimes/{args.runtime_id}-DEFAULT"
    deadline = time.monotonic() + args.timeout_seconds
    trace_spans: list[Mapping[str, Any]] = []
    while time.monotonic() < deadline:
        streams = logs.describe_log_streams(
            logGroupName=group_name, logStreamNamePrefix="spans"
        ).get("logStreams", [])
        messages: list[str] = []
        for stream in streams:
            events = logs.get_log_events(
                logGroupName=group_name,
                logStreamName=stream["logStreamName"],
                startTime=args.start_time_ms,
                startFromHead=True,
            ).get("events", [])
            messages.extend(event["message"] for event in events)
        trace_spans = _matching_spans(messages, args.trace_id)
        if any(item.get("name") == PROBE_SPAN_NAME for item in trace_spans):
            break
        time.sleep(args.poll_seconds)
    probe_spans = [item for item in trace_spans if item.get("name") == PROBE_SPAN_NAME]
    if len(probe_spans) != 1:
        raise RuntimeError(f"expected one managed probe span, found {len(probe_spans)}")
    probe = probe_spans[0]
    if probe.get("spanId") != args.span_id:
        raise RuntimeError("managed span ID does not match the Runtime response")
    attributes = probe.get("attributes")
    if not isinstance(attributes, Mapping):
        raise RuntimeError("managed probe span has no attribute map")
    expected = {
        "quorum.probe_id": args.probe_id,
        "quorum.organization_id": "org_synthetic",
        "quorum.session_id": args.session_id,
        "quorum.data_classification": "synthetic",
    }
    if any(attributes.get(key) != value for key, value in expected.items()):
        raise RuntimeError("managed probe span is missing an expected allowlisted attribute")
    unexpected = sorted(
        key
        for key in attributes
        if key.startswith("quorum.") and key not in ALLOWED_QUORUM_ATTRIBUTES
    )
    if unexpected:
        raise RuntimeError(f"managed probe span has unexpected Quorum attributes: {unexpected}")
    serialized = json.dumps(trace_spans, separators=(",", ":"), sort_keys=True)
    leaked = [marker for marker in FORBIDDEN_MARKERS if marker.lower() in serialized.lower()]
    if leaked:
        raise RuntimeError(f"managed trace contains forbidden markers: {leaked}")
    print(f"managed_span_name={PROBE_SPAN_NAME}")
    print(f"managed_trace_id={args.trace_id}")
    print(f"managed_span_id={args.span_id}")
    print(f"managed_trace_span_count={len(trace_spans)}")
    print("data_classification=synthetic")
    print("forbidden_content_matches=0")
    print("model_calls=0")
    print("memory_events=0")
    print("gateway_tool_calls=0")
    print("external_side_effect_calls=0")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--account-id", required=True)
    prepare_parser.add_argument("--region", required=True)
    prepare_parser.add_argument("--policy-name", required=True)
    prepare_parser.add_argument("--state-file", type=Path, required=True)
    prepare_parser.set_defaults(handler=prepare)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--state-file", type=Path, required=True)
    restore_parser.set_defaults(handler=restore)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--region", required=True)
    verify_parser.add_argument("--runtime-id", required=True)
    verify_parser.add_argument("--probe-id", required=True)
    verify_parser.add_argument("--session-id", required=True)
    verify_parser.add_argument("--trace-id", required=True)
    verify_parser.add_argument("--span-id", required=True)
    verify_parser.add_argument("--start-time-ms", type=int, required=True)
    verify_parser.add_argument("--timeout-seconds", type=int, default=600)
    verify_parser.add_argument("--poll-seconds", type=int, default=10)
    verify_parser.set_defaults(handler=verify)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
