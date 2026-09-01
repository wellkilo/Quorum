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
from botocore.exceptions import ClientError

POLICY_PREFIX = "QuorumTransactionSearchVerification-"
MANAGED_LOG_GROUPS = ("aws/spans", "/aws/application-signals/data")
APPLICATION_SIGNALS_ROLE = "AWSServiceRoleForCloudWatchApplicationSignals"
APPLICATION_SIGNALS_SERVICE_NAME = "application-signals.cloudwatch.amazonaws.com"
APPLICATION_SIGNALS_CHANNEL_FRAGMENT = "channel/aws-service-channel/application-signals/"
DEFAULT_TRANSITION_TIMEOUT_SECONDS = 900
DEFAULT_POLL_SECONDS = 5
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


def _role_exists(iam: Any, name: str) -> bool:
    try:
        iam.get_role(RoleName=name)
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NoSuchEntity":
            return False
        raise


def _application_signals_channel_arns(cloudtrail: Any) -> set[str]:
    matches: set[str] = set()
    next_token: str | None = None
    while True:
        request = {"NextToken": next_token} if next_token else {}
        response = cloudtrail.list_channels(**request)
        matches.update(
            item["ChannelArn"]
            for item in response.get("Channels", [])
            if APPLICATION_SIGNALS_CHANNEL_FRAGMENT in item.get("ChannelArn", "")
        )
        next_token = response.get("NextToken")
        if not next_token:
            return matches


def _wait_for_destination(
    xray: Any, expected_destination: str, timeout_seconds: int, poll_seconds: int
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = xray.get_trace_segment_destination()
        if current.get("Destination") == expected_destination and current.get("Status") == "ACTIVE":
            return
        time.sleep(poll_seconds)
    raise RuntimeError(
        f"trace destination {expected_destination} did not become active "
        f"within {timeout_seconds} seconds"
    )


def prepare(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"QuorumTransactionSearchVerification-[A-Za-z0-9-]+", args.policy_name):
        raise ValueError("policy name must be unique and use the Quorum verification prefix")
    session = boto3.Session(region_name=args.region)
    logs = session.client("logs")
    xray = session.client("xray")
    iam = session.client("iam")
    cloudtrail = session.client("cloudtrail")
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
        "application_signals_role_preexisting": _role_exists(iam, APPLICATION_SIGNALS_ROLE),
        "application_signals_role_created": False,
        "application_signals_channel_arns_preexisting": sorted(
            _application_signals_channel_arns(cloudtrail)
        ),
        "region": args.region,
    }
    _write_state(args.state_file, state)
    if not state["application_signals_role_preexisting"]:
        iam.create_service_linked_role(AWSServiceName=APPLICATION_SIGNALS_SERVICE_NAME)
        state["application_signals_role_created"] = True
        _write_state(args.state_file, state)

    logs.put_resource_policy(
        policyName=args.policy_name,
        policyDocument=_transaction_policy(args.account_id, args.region),
    )
    state["policy_created"] = True
    _write_state(args.state_file, state)
    if destination != "CloudWatchLogs":
        _wait_for_destination(xray, destination, args.transition_timeout_seconds, args.poll_seconds)
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
    xray.update_indexing_rule(
        Name="Default", Rule={"Probabilistic": {"DesiredSamplingPercentage": 0.0}}
    )

    _wait_for_destination(
        xray, "CloudWatchLogs", args.transition_timeout_seconds, args.poll_seconds
    )
    print("transaction_search_destination=CloudWatchLogs")
    print("transaction_search_indexing_percentage=0.0")
    print(f"transaction_search_policy={args.policy_name}")


def restore(args: argparse.Namespace) -> None:
    if not args.state_file.is_file():
        print("observability_state=absent")
        return
    state = json.loads(args.state_file.read_text(encoding="utf-8"))
    session = boto3.Session(region_name=state["region"])
    logs = session.client("logs")
    xray = session.client("xray")
    iam = session.client("iam")
    cloudtrail = session.client("cloudtrail")
    errors: list[str] = []
    current_destination: Mapping[str, Any] | None = None
    current_percentage: float | None = None
    try:
        current_destination = xray.get_trace_segment_destination()
        current_rules = xray.get_indexing_rules().get("IndexingRules", [])
        current_default = next(item for item in current_rules if item.get("Name") == "Default")
        current_percentage = current_default["Rule"]["Probabilistic"]["DesiredSamplingPercentage"]
    except Exception as exc:  # cleanup must continue across independent resources
        errors.append(f"current X-Ray settings read failed: {type(exc).__name__}")
    try:
        if (
            current_percentage is not None
            and current_percentage != state["previous_indexing_percentage"]
        ):
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
    destination_restored = False
    try:
        if current_destination is not None:
            current_name = current_destination.get("Destination")
            if current_destination.get("Status") != "ACTIVE":
                _wait_for_destination(
                    xray, current_name, args.transition_timeout_seconds, args.poll_seconds
                )
            if current_name != state["previous_destination"]:
                xray.update_trace_segment_destination(Destination=state["previous_destination"])
            _wait_for_destination(
                xray,
                state["previous_destination"],
                args.transition_timeout_seconds,
                args.poll_seconds,
            )
            destination_restored = True
    except Exception as exc:  # cleanup must continue across independent resources
        errors.append(f"destination restore failed: {type(exc).__name__}")
    if state.get("policy_created"):
        try:
            logs.delete_resource_policy(policyName=state["policy_name"])
        except Exception as exc:  # cleanup must report, not hide, incomplete rollback
            errors.append(f"policy deletion failed: {type(exc).__name__}")
    try:
        current_rules = xray.get_indexing_rules().get("IndexingRules", [])
        current_default = next(item for item in current_rules if item.get("Name") == "Default")
        restored_percentage = current_default["Rule"]["Probabilistic"]["DesiredSamplingPercentage"]
        if restored_percentage != state["previous_indexing_percentage"]:
            errors.append("indexing percentage restoration could not be verified")
    except Exception as exc:  # cleanup must continue across independent resources
        errors.append(f"X-Ray restoration verification failed: {type(exc).__name__}")
    try:
        remaining_policies = logs.describe_resource_policies(policyScope="ACCOUNT").get(
            "resourcePolicies", []
        )
        if any(item.get("policyName") == state["policy_name"] for item in remaining_policies):
            errors.append("temporary Transaction Search policy still exists after cleanup")
    except Exception as exc:  # cleanup must continue across independent resources
        errors.append(f"policy deletion verification failed: {type(exc).__name__}")
    preexisting = state.get("managed_log_groups_preexisting", {})
    removed_log_groups = 0
    if destination_restored:
        for group_name in MANAGED_LOG_GROUPS:
            if not isinstance(preexisting, Mapping) or preexisting.get(group_name, True):
                continue
            try:
                if _log_group_exists(logs, group_name):
                    logs.delete_log_group(logGroupName=group_name)
                    removed_log_groups += 1
                if _log_group_exists(logs, group_name):
                    errors.append(f"temporary managed log group still exists: {group_name}")
            except Exception as exc:  # cleanup must continue across independent resources
                errors.append(
                    f"managed log group cleanup failed ({group_name}): {type(exc).__name__}"
                )
    previous_channels = set(state.get("application_signals_channel_arns_preexisting", []))
    new_channels: set[str] = set()
    try:
        new_channels = _application_signals_channel_arns(cloudtrail) - previous_channels
    except Exception as exc:  # channel inspection must not block role cleanup
        errors.append(f"Application Signals channel inspection failed: {type(exc).__name__}")
    role_removed = False
    try:
        role_created = state.get("application_signals_role_created", False)
        if destination_restored and role_created and _role_exists(iam, APPLICATION_SIGNALS_ROLE):
            task = iam.delete_service_linked_role(RoleName=APPLICATION_SIGNALS_ROLE)
            task_id = task["DeletionTaskId"]
            for _attempt in range(30):
                deletion = iam.get_service_linked_role_deletion_status(DeletionTaskId=task_id)
                status = deletion["Status"]
                if status == "SUCCEEDED":
                    role_removed = True
                    break
                if status == "FAILED":
                    reason = deletion.get("Reason", "unspecified")
                    errors.append(
                        f"Application Signals service-linked role cleanup failed: {reason}"
                    )
                    break
                time.sleep(2)
            else:
                errors.append("Application Signals service-linked role cleanup timed out")
        if destination_restored and role_created and _role_exists(iam, APPLICATION_SIGNALS_ROLE):
            errors.append("temporary Application Signals role still exists after cleanup")
    except Exception as exc:
        errors.append(f"Application Signals role cleanup failed: {type(exc).__name__}")
    if errors:
        raise RuntimeError("; ".join(errors))
    print(f"transaction_search_destination_restored={state['previous_destination']}")
    print(f"transaction_search_indexing_restored={state['previous_indexing_percentage']}")
    print("transaction_search_policy_removed=true")
    print(f"temporary_managed_log_groups_removed={removed_log_groups}")
    print(f"service_managed_application_signals_channels_retained={len(new_channels)}")
    print(f"temporary_application_signals_role_removed={str(role_removed).lower()}")


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
    prepare_parser.add_argument(
        "--transition-timeout-seconds", type=int, default=DEFAULT_TRANSITION_TIMEOUT_SECONDS
    )
    prepare_parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    prepare_parser.set_defaults(handler=prepare)

    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--state-file", type=Path, required=True)
    restore_parser.add_argument(
        "--transition-timeout-seconds", type=int, default=DEFAULT_TRANSITION_TIMEOUT_SECONDS
    )
    restore_parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
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
