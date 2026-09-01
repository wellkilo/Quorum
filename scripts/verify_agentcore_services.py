#!/usr/bin/env python3
"""Verify short-lived AgentCore Memory and Gateway resources, then remove them."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from quorum.agentcore_resources import (
    RESOURCE_TAGS,
    create_gateway_for_verification,
    create_memory_for_verification,
    delete_gateway_and_wait,
    delete_memory_and_wait,
    discover_gateway_tools,
)


def _names(run_id: str) -> tuple[str, str, str]:
    safe_run_id = "".join(character for character in run_id if character.isalnum())[-20:]
    if not safe_run_id:
        raise ValueError("run ID must contain an alphanumeric character")
    return (
        f"QuorumMemory{safe_run_id}",
        f"QuorumGateway-{safe_run_id}",
        f"QuorumExecutionTools-{safe_run_id}",
    )


def _ensure_artifact_bucket(s3: Any, *, bucket: str, account_id: str, region: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket, ExpectedBucketOwner=account_id)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status != 404:
            raise
        request: dict[str, object] = {"Bucket": bucket}
        if region != "us-east-1":
            request["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**request)
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
                    "BucketKeyEnabled": False,
                }
            ]
        },
    )
    s3.put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={
            "Rules": [
                {
                    "ID": "ExpireQuorumVerificationArtifacts",
                    "Status": "Enabled",
                    "Filter": {"Prefix": "verification/"},
                    "Expiration": {"Days": 1},
                }
            ]
        },
    )


def _create_lambda(
    lambda_client: Any,
    *,
    function_name: str,
    role_arn: str,
    bucket: str,
    key: str,
) -> str:
    response = lambda_client.create_function(
        FunctionName=function_name,
        Description="Short-lived Quorum AgentCore Gateway target; execution disabled",
        Runtime="python3.13",
        Architectures=["arm64"],
        Role=role_arn,
        Handler="quorum.gateway.lambda_handler",
        Code={"S3Bucket": bucket, "S3Key": key},
        Timeout=15,
        MemorySize=256,
        Environment={
            "Variables": {
                "QUORUM_EXECUTION_ENABLED": "false",
                "QUORUM_DATABASE_URL": "sqlite+pysqlite:////tmp/quorum-gateway.sqlite3",
            }
        },
        Tags=RESOURCE_TAGS,
    )
    lambda_client.get_waiter("function_active_v2").wait(
        FunctionName=function_name,
        WaiterConfig={"Delay": 2, "MaxAttempts": 90},
    )
    arn = response.get("FunctionArn")
    if not isinstance(arn, str) or not arn:
        raise RuntimeError("CreateFunction response is missing FunctionArn")
    return arn


def _verify_lambda_gate(lambda_client: Any, function_name: str) -> None:
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=b"{}",
    )
    payload = json.loads(response["Payload"].read())
    if response.get("FunctionError") is None:
        raise RuntimeError("Gateway Lambda unexpectedly accepted a tool call")
    if "Gateway execution is disabled" not in str(payload.get("errorMessage", "")):
        raise RuntimeError("Gateway Lambda did not return the execution cost/safety gate")


def verify(args: argparse.Namespace) -> None:
    memory_name, gateway_name, function_name = _names(args.run_id)
    session = boto3.Session(region_name=args.region)
    sts = session.client("sts")
    identity = sts.get_caller_identity()
    if identity.get("Account") != args.account_id:
        raise RuntimeError("AWS caller account does not match --account-id")
    control = session.client("bedrock-agentcore-control")
    lambda_client = session.client("lambda")
    s3 = session.client("s3")
    gateway_id: str | None = None
    target_id: str | None = None
    memory_id: str | None = None
    try:
        _ensure_artifact_bucket(
            s3, bucket=args.artifact_bucket, account_id=args.account_id, region=args.region
        )
        s3.upload_file(str(args.artifact), args.artifact_bucket, args.artifact_key)
        lambda_arn = _create_lambda(
            lambda_client,
            function_name=function_name,
            role_arn=args.lambda_role_arn,
            bucket=args.artifact_bucket,
            key=args.artifact_key,
        )
        _verify_lambda_gate(lambda_client, function_name)
        print("lambda_gate=execution-disabled")

        memory = create_memory_for_verification(control, name=memory_name)
        memory_id = memory.memory_id
        print(
            f"memory_status={memory.status} strategies={','.join(memory.strategy_names)} "
            f"events_created={memory.event_count}"
        )

        gateway_id, gateway_url, target_id = create_gateway_for_verification(
            control,
            name=gateway_name,
            role_arn=args.gateway_role_arn,
            lambda_arn=lambda_arn,
        )
        tool_names = discover_gateway_tools(endpoint=gateway_url, region_name=args.region)
        print(
            f"gateway_status=READY tools={','.join(tool_names)} tool_calls=0 authentication=AWS_IAM"
        )
    finally:
        cleanup(
            args,
            control=control,
            lambda_client=lambda_client,
            s3=s3,
            gateway_id=gateway_id,
            target_id=target_id,
            memory_id=memory_id,
            function_name=function_name,
        )


def cleanup(
    args: argparse.Namespace,
    *,
    control: Any | None = None,
    lambda_client: Any | None = None,
    s3: Any | None = None,
    logs: Any | None = None,
    gateway_id: str | None = None,
    target_id: str | None = None,
    memory_id: str | None = None,
    function_name: str | None = None,
) -> None:
    memory_name, gateway_name, expected_function_name = _names(args.run_id)
    session = boto3.Session(region_name=args.region)
    control = control or session.client("bedrock-agentcore-control")
    lambda_client = lambda_client or session.client("lambda")
    s3 = s3 or session.client("s3")
    logs = logs or session.client("logs")
    function_name = function_name or expected_function_name
    failures: list[str] = []

    try:
        if gateway_id is None:
            gateways = control.list_gateways(maxResults=100).get("items", [])
            match = next((item for item in gateways if item.get("name") == gateway_name), None)
            gateway_id = match.get("gatewayId") if isinstance(match, dict) else None
        if isinstance(gateway_id, str):
            if target_id is None:
                targets = control.list_gateway_targets(
                    gatewayIdentifier=gateway_id, maxResults=100
                ).get("items", [])
                match = next(
                    (item for item in targets if item.get("name") == "quorum-execution"),
                    None,
                )
                target_id = match.get("targetId") if isinstance(match, dict) else None
            delete_gateway_and_wait(
                control, gateway_id, target_id if isinstance(target_id, str) else None
            )
    except Exception as exc:
        failures.append(f"gateway:{type(exc).__name__}")

    try:
        if memory_id is None:
            for summary in control.list_memories(maxResults=100).get("memories", []):
                candidate_id = summary.get("id")
                if isinstance(candidate_id, str) and candidate_id.startswith(memory_name):
                    memory_id = candidate_id
                    break
        if isinstance(memory_id, str):
            delete_memory_and_wait(control, memory_id)
    except Exception as exc:
        failures.append(f"memory:{type(exc).__name__}")

    try:
        lambda_client.delete_function(FunctionName=function_name)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            failures.append(f"lambda:{type(exc).__name__}")
    try:
        logs.delete_log_group(logGroupName=f"/aws/lambda/{function_name}")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            failures.append(f"logs:{type(exc).__name__}")
    try:
        s3.delete_object(Bucket=args.artifact_bucket, Key=args.artifact_key)
        s3.delete_bucket(Bucket=args.artifact_bucket, ExpectedBucketOwner=args.account_id)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            failures.append(f"s3:{type(exc).__name__}")
    if failures:
        raise RuntimeError(f"incomplete cleanup: {','.join(failures)}")
    print("cleanup=complete")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("verify", "cleanup"))
    parser.add_argument("--region", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--artifact-bucket", required=True)
    parser.add_argument("--artifact-key", required=True)
    parser.add_argument("--lambda-role-arn", required=True)
    parser.add_argument("--gateway-role-arn", required=True)
    args = parser.parse_args()
    if args.operation == "verify" and (args.artifact is None or not args.artifact.is_file()):
        parser.error("verify requires an existing --artifact")
    return args


def main() -> int:
    args = parse_args()
    if args.operation == "verify":
        verify(args)
    else:
        cleanup(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
