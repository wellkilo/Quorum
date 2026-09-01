"""Short-lived AgentCore Memory and Gateway verification lifecycles.

The verifier intentionally creates no Memory events and invokes no Gateway tools. It proves the
managed resources and typed MCP discovery path without triggering a model or external side effect.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from quorum.gateway import (
    GATEWAY_TOOL_NAMES,
    gateway_create_request,
    gateway_executor_tools,
    gateway_target_request,
)
from quorum.memory import memory_strategy_definitions

RESOURCE_TAGS = {
    "Project": "Quorum",
    "DataClassification": "SyntheticOnly",
    "CostMode": "ZeroModel",
}
GATEWAY_FAILURE_STATUSES = frozenset({"FAILED", "UPDATE_UNSUCCESSFUL"})
TARGET_FAILURE_STATUSES = frozenset(
    {
        *GATEWAY_FAILURE_STATUSES,
        "SYNCHRONIZE_UNSUCCESSFUL",
        "CREATE_PENDING_AUTH",
        "UPDATE_PENDING_AUTH",
        "SYNCHRONIZE_PENDING_AUTH",
    }
)


class AgentCoreControl(Protocol):
    def create_memory(self, **kwargs: object) -> dict[str, Any]: ...

    def get_memory(self, **kwargs: object) -> dict[str, Any]: ...

    def delete_memory(self, **kwargs: object) -> dict[str, Any]: ...

    def create_gateway(self, **kwargs: object) -> dict[str, Any]: ...

    def get_gateway(self, **kwargs: object) -> dict[str, Any]: ...

    def delete_gateway(self, **kwargs: object) -> dict[str, Any]: ...

    def create_gateway_target(self, **kwargs: object) -> dict[str, Any]: ...

    def get_gateway_target(self, **kwargs: object) -> dict[str, Any]: ...

    def delete_gateway_target(self, **kwargs: object) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class MemoryVerification:
    memory_id: str
    status: str
    strategy_names: tuple[str, ...]
    event_count: int = 0


@dataclass(frozen=True, slots=True)
class GatewayVerification:
    gateway_id: str
    gateway_url: str
    target_id: str
    status: str
    tool_names: tuple[str, ...]
    tool_call_count: int = 0


def create_memory_for_verification(
    control: AgentCoreControl,
    *,
    name: str,
    timeout_seconds: float = 300,
    poll_interval_seconds: float = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> MemoryVerification:
    """Create an empty Memory, wait for ACTIVE, and verify its two strategy names."""

    created = control.create_memory(
        name=name,
        description="Short-lived Quorum strategy verification with zero events",
        eventExpiryDuration=7,
        memoryStrategies=memory_strategy_definitions(),
        tags=RESOURCE_TAGS,
    )
    memory = _mapping(created.get("memory"), "CreateMemory response is missing memory")
    memory_id = _string(memory.get("id", memory.get("memoryId")), "memory ID")
    active = _wait_for_status(
        lambda: control.get_memory(memoryId=memory_id),
        resource_label="Memory",
        success_status="ACTIVE",
        failure_statuses={"FAILED"},
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        sleep=sleep,
        response_object_key="memory",
    )
    active_memory = _mapping(active.get("memory"), "GetMemory response is missing memory")
    raw_strategies = active_memory.get("strategies", active_memory.get("memoryStrategies", []))
    strategy_names = _strategy_names(raw_strategies)
    expected = ("QuorumFacts", "QuorumSummaries")
    if strategy_names != expected:
        raise RuntimeError(
            f"Memory strategy mismatch: expected {expected}, observed {strategy_names}"
        )
    return MemoryVerification(
        memory_id=memory_id,
        status="ACTIVE",
        strategy_names=strategy_names,
    )


def delete_memory_and_wait(
    control: AgentCoreControl,
    memory_id: str,
    *,
    timeout_seconds: float = 300,
    poll_interval_seconds: float = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    def get_memory() -> dict[str, Any]:
        return control.get_memory(memoryId=memory_id)

    if _resource_is_missing(get_memory):
        return
    if not _resource_is_deleting(get_memory(), response_object_key="memory"):
        try:
            control.delete_memory(memoryId=memory_id)
        except ClientError as exc:
            if not _delete_is_already_in_progress(exc, get_memory, response_object_key="memory"):
                raise
    _wait_until_missing(
        get_memory,
        resource_label="Memory",
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        sleep=sleep,
    )


def create_gateway_for_verification(
    control: AgentCoreControl,
    *,
    name: str,
    role_arn: str,
    lambda_arn: str,
    timeout_seconds: float = 300,
    poll_interval_seconds: float = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str, str]:
    """Create an IAM MCP Gateway and its typed Lambda target, both in READY state."""

    created = control.create_gateway(**gateway_create_request(gateway_name=name, role_arn=role_arn))
    gateway_id = _string(created.get("gatewayId"), "gateway ID")
    gateway_url = _string(created.get("gatewayUrl"), "gateway URL")
    _wait_for_status(
        lambda: control.get_gateway(gatewayIdentifier=gateway_id),
        resource_label="Gateway",
        success_status="READY",
        failure_statuses=GATEWAY_FAILURE_STATUSES,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        sleep=sleep,
    )
    target = control.create_gateway_target(
        **gateway_target_request(gateway_id=gateway_id, lambda_arn=lambda_arn)
    )
    target_id = _string(target.get("targetId"), "gateway target ID")
    _wait_for_status(
        lambda: control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id),
        resource_label="Gateway target",
        success_status="READY",
        failure_statuses=TARGET_FAILURE_STATUSES,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        sleep=sleep,
    )
    return gateway_id, gateway_url, target_id


def discover_gateway_tools(*, endpoint: str, region_name: str) -> tuple[str, ...]:
    """Execute MCP initialize/tools-list only and reject any unexpected tool surface."""

    with gateway_executor_tools(endpoint=endpoint, region_name=region_name) as tools:
        names = tuple(tool.tool_name for tool in tools)
    if names != GATEWAY_TOOL_NAMES:
        raise RuntimeError(
            f"Gateway tool mismatch: expected {GATEWAY_TOOL_NAMES}, observed {names}"
        )
    return names


def delete_gateway_and_wait(
    control: AgentCoreControl,
    gateway_id: str,
    target_id: str | None,
    *,
    timeout_seconds: float = 300,
    poll_interval_seconds: float = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if target_id is not None:

        def get_gateway_target() -> dict[str, Any]:
            return control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)

        if not _resource_is_missing(get_gateway_target) and not _resource_is_deleting(
            get_gateway_target()
        ):
            try:
                control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
            except ClientError as exc:
                if not _delete_is_already_in_progress(exc, get_gateway_target):
                    raise
        _wait_until_missing(
            get_gateway_target,
            resource_label="Gateway target",
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            sleep=sleep,
        )

    def get_gateway() -> dict[str, Any]:
        return control.get_gateway(gatewayIdentifier=gateway_id)

    if _resource_is_missing(get_gateway):
        return
    if not _resource_is_deleting(get_gateway()):
        try:
            control.delete_gateway(gatewayIdentifier=gateway_id)
        except ClientError as exc:
            if not _delete_is_already_in_progress(exc, get_gateway):
                raise
    _wait_until_missing(
        get_gateway,
        resource_label="Gateway",
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        sleep=sleep,
    )


def _wait_for_status(
    getter: Callable[[], dict[str, Any]],
    *,
    resource_label: str,
    success_status: str,
    failure_statuses: Sequence[str] | set[str] | frozenset[str],
    timeout_seconds: float,
    poll_interval_seconds: float,
    sleep: Callable[[float], None],
    response_object_key: str | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        response = getter()
        status_source: Mapping[str, Any] = response
        if response_object_key is not None:
            status_source = _mapping(
                response.get(response_object_key),
                f"{resource_label} response is missing {response_object_key}",
            )
        status = _string(status_source.get("status"), f"{resource_label} status")
        if status == success_status:
            return response
        if status in failure_statuses:
            reasons = status_source.get("statusReasons", status_source.get("failureReason", []))
            raise RuntimeError(f"{resource_label} entered {status}: {reasons}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{resource_label} did not reach {success_status}")
        sleep(poll_interval_seconds)


def _wait_until_missing(
    getter: Callable[[], dict[str, Any]],
    *,
    resource_label: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            getter()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                return
            raise
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{resource_label} was not deleted")
        sleep(poll_interval_seconds)


def _resource_is_missing(getter: Callable[[], dict[str, Any]]) -> bool:
    try:
        getter()
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return True
        raise
    return False


def _resource_is_deleting(
    response: Mapping[str, Any], *, response_object_key: str | None = None
) -> bool:
    status_source = response
    if response_object_key is not None:
        status_source = _mapping(
            response.get(response_object_key),
            f"Resource response is missing {response_object_key}",
        )
    status = status_source.get("status")
    return isinstance(status, str) and status in {"DELETING", "DELETE_PENDING"}


def _delete_is_already_in_progress(
    exc: ClientError,
    getter: Callable[[], dict[str, Any]],
    *,
    response_object_key: str | None = None,
) -> bool:
    if exc.response.get("Error", {}).get("Code") != "ValidationException":
        return False
    try:
        response = getter()
    except ClientError as get_exc:
        error = get_exc.response.get("Error")
        code = error.get("Code") if isinstance(error, Mapping) else None
        return isinstance(code, str) and code == "ResourceNotFoundException"
    return _resource_is_deleting(response, response_object_key=response_object_key)


def _strategy_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise RuntimeError("Memory strategies must be a list")
    names: list[str] = []
    for strategy in value:
        item = _mapping(strategy, "Memory strategy must be an object")
        name = item.get("name")
        if not isinstance(name, str):
            for key in ("semanticMemoryStrategy", "summaryMemoryStrategy"):
                nested = item.get(key)
                if isinstance(nested, Mapping) and isinstance(nested.get("name"), str):
                    name = nested["name"]
                    break
        names.append(_string(name, "memory strategy name"))
    return tuple(sorted(names))


def _mapping(value: object, message: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(message)
    return cast(Mapping[str, Any], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"AgentCore response is missing {label}")
    return value
