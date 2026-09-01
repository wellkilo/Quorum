"""AgentCore Gateway MCP client, target schema, and Lambda execution adapter."""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Protocol, cast

import boto3  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

from quorum.database import DatabaseSettings, create_database_engine
from quorum.execution import ActionExecutionService, build_action_execution_service
from quorum.models import CalendarActionInput, EmailDraftActionInput, FormActionInput

GATEWAY_TOOL_NAMES = (
    "calendar_create_tentative_event",
    "gmail_create_draft",
    "forms_create_response_request",
)

_TOOL_DESCRIPTIONS = {
    "calendar_create_tentative_event": "Create a reversible tentative Google Calendar event.",
    "gmail_create_draft": "Create a reversible Gmail draft without sending it.",
    "forms_create_response_request": "Create a reversible Google Form response request.",
}

_TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "calendar_create_tentative_event": CalendarActionInput,
    "gmail_create_draft": EmailDraftActionInput,
    "forms_create_response_request": FormActionInput,
}


class GatewayConfigurationError(ValueError):
    """Raised when the Gateway endpoint or region is missing or unsafe."""


class LambdaClientContext(Protocol):
    custom: Mapping[str, str]


class LambdaContext(Protocol):
    client_context: LambdaClientContext | None


def gateway_tool_definitions() -> list[dict[str, object]]:
    """Return the three Lambda target definitions accepted by AgentCore Gateway."""

    definitions: list[dict[str, object]] = []
    for name, model in _TOOL_INPUT_MODELS.items():
        schema = model.model_json_schema()
        definitions.append(
            {
                "name": name,
                "description": _TOOL_DESCRIPTIONS[name],
                "inputSchema": _gateway_schema(schema, schema),
            }
        )
    return definitions


def gateway_create_request(*, gateway_name: str, role_arn: str) -> dict[str, object]:
    """Return the reviewed AWS_IAM MCP Gateway control-plane request."""

    return {
        "name": gateway_name,
        "description": "Quorum reversible execution tools",
        "roleArn": role_arn,
        "protocolType": "MCP",
        "authorizerType": "AWS_IAM",
        "tags": {
            "Project": "Quorum",
            "DataClassification": "SyntheticOnly",
            "CostMode": "ZeroModel",
        },
    }


def gateway_target_request(
    *, gateway_id: str, lambda_arn: str, target_name: str = "quorum-execution"
) -> dict[str, object]:
    """Return the narrow Lambda target request shared by CLI and cloud verification."""

    return {
        "gatewayIdentifier": gateway_id,
        "name": target_name,
        "description": "Three reversible Quorum action tools",
        "targetConfiguration": {
            "mcp": {
                "lambda": {
                    "lambdaArn": lambda_arn,
                    "toolSchema": {"inlinePayload": gateway_tool_definitions()},
                }
            }
        },
        "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    }


def _gateway_schema(schema: Mapping[str, Any], root: Mapping[str, Any]) -> dict[str, object]:
    """Keep only fields supported by Gateway's recursive SchemaDefinition shape."""

    result: dict[str, object] = {"type": schema.get("type", "object")}
    description = schema.get("description")
    if isinstance(description, str):
        result["description"] = description
    properties = schema.get("properties")
    if isinstance(properties, dict):
        result["properties"] = {
            key: _gateway_schema(_resolve_schema(property_schema, root), root)
            for key, property_schema in properties.items()
            if isinstance(key, str) and isinstance(property_schema, dict)
        }
    required = schema.get("required")
    if isinstance(required, list):
        result["required"] = [item for item in required if isinstance(item, str)]
    items = schema.get("items")
    if isinstance(items, dict):
        result["items"] = _gateway_schema(_resolve_schema(items, root), root)
    return result


def _resolve_schema(value: Mapping[str, Any], root: Mapping[str, Any]) -> Mapping[str, Any]:
    reference = value.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        definitions = root.get("$defs", {})
        resolved = (
            definitions.get(reference.rsplit("/", 1)[-1]) if isinstance(definitions, dict) else None
        )
        if isinstance(resolved, dict):
            return resolved
    any_of = value.get("anyOf")
    if isinstance(any_of, list):
        concrete = [
            item for item in any_of if isinstance(item, dict) and item.get("type") != "null"
        ]
        if len(concrete) == 1:
            return _resolve_schema(concrete[0], root)
    return value


@contextmanager
def gateway_executor_tools(
    *, endpoint: str, region_name: str, profile_name: str | None = None
) -> Iterator[list[MCPAgentTool]]:
    """Connect to an IAM-authenticated AgentCore Gateway and expose stable local names."""

    if not endpoint.startswith("https://"):
        raise GatewayConfigurationError("AgentCore Gateway endpoint must use HTTPS")
    if not region_name:
        raise GatewayConfigurationError("AgentCore Gateway region is required")
    from mcp_proxy_for_aws.client import (  # type: ignore[import-untyped]
        aws_iam_streamablehttp_client,
    )
    from strands.tools.mcp import MCPClient
    from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

    client = MCPClient(
        lambda: aws_iam_streamablehttp_client(
            endpoint=endpoint,
            aws_service="bedrock-agentcore",
            aws_region=region_name,
            aws_profile=profile_name,
            timeout=30,
            sse_read_timeout=120,
        )
    )
    with client:
        selected: dict[str, MCPAgentTool] = {}
        for remote_tool in client.list_tools_sync():
            stable_name = remote_tool.tool_name.rsplit("___", 1)[-1]
            if stable_name in GATEWAY_TOOL_NAMES:
                if stable_name in selected:
                    raise GatewayConfigurationError(f"duplicate Gateway tool: {stable_name}")
                selected[stable_name] = MCPAgentTool(
                    remote_tool.mcp_tool, client, name_override=stable_name
                )
        missing = set(GATEWAY_TOOL_NAMES) - selected.keys()
        if missing:
            raise GatewayConfigurationError(
                f"Gateway is missing required tools: {', '.join(sorted(missing))}"
            )
        yield [selected[name] for name in GATEWAY_TOOL_NAMES]


@lru_cache(maxsize=1)
def _get_lambda_service() -> ActionExecutionService:
    engine = create_database_engine(DatabaseSettings.from_environment())
    return build_action_execution_service(engine)


def gateway_lambda_handler(event: object, context: LambdaContext) -> dict[str, object]:
    """Validate a Gateway Lambda target event and call the existing execution service."""

    if os.environ.get("QUORUM_EXECUTION_ENABLED", "").strip().lower() not in {"1", "true"}:
        raise RuntimeError(
            "Gateway execution is disabled; enable it only with configured providers"
        )
    if not isinstance(event, dict):
        raise ValueError("Gateway tool arguments must be an object")
    custom = context.client_context.custom if context.client_context is not None else {}
    remote_name = custom.get("bedrockAgentCoreToolName", "")
    tool_name = remote_name.rsplit("___", 1)[-1]
    if tool_name not in GATEWAY_TOOL_NAMES:
        raise ValueError("unsupported Gateway tool")
    try:
        model = _TOOL_INPUT_MODELS[tool_name].model_validate(event)
    except ValidationError as exc:
        raise ValueError("invalid Gateway tool arguments") from exc

    service = _get_lambda_service()
    if isinstance(model, CalendarActionInput):
        receipt = service.create_tentative_event(model)
    elif isinstance(model, EmailDraftActionInput):
        receipt = service.create_email_draft(model)
    elif isinstance(model, FormActionInput):
        receipt = service.create_response_request(model)
    else:  # pragma: no cover - the model table is closed above
        raise AssertionError("unreachable Gateway model")
    return receipt.model_dump(mode="json")


# AWS Lambda's conventional entrypoint name.
lambda_handler = gateway_lambda_handler


def provision_gateway(
    *,
    region_name: str,
    gateway_name: str,
    role_arn: str,
    lambda_arn: str,
    target_name: str = "quorum-execution",
) -> dict[str, str]:
    """Create an IAM-authenticated MCP Gateway and its Lambda target."""

    client = boto3.client("bedrock-agentcore-control", region_name=region_name)
    gateway = client.create_gateway(
        **gateway_create_request(gateway_name=gateway_name, role_arn=role_arn)
    )
    gateway_id = cast(str, gateway["gatewayId"])
    target = client.create_gateway_target(
        **gateway_target_request(
            gateway_id=gateway_id, lambda_arn=lambda_arn, target_name=target_name
        )
    )
    return {
        "gateway_id": gateway_id,
        "gateway_url": cast(str, gateway["gatewayUrl"]),
        "target_id": cast(str, target["targetId"]),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision Quorum's AgentCore Gateway target.")
    parser.add_argument("--region", required=True)
    parser.add_argument("--gateway-name", default="quorum-execution")
    parser.add_argument("--role-arn", required=True)
    parser.add_argument("--lambda-arn", required=True)
    args = parser.parse_args(argv)
    result = provision_gateway(
        region_name=args.region,
        gateway_name=args.gateway_name,
        role_arn=args.role_arn,
        lambda_arn=args.lambda_arn,
    )
    for key, value in result.items():
        print(f"{key}={value}")
    return 0
