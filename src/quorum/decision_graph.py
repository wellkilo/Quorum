"""Five-node Strands Graph wiring for Quorum's deterministic decision path."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from strands import Agent, tool
from strands.agent.agent_result import AgentResult
from strands.models.model import Model
from strands.multiagent import GraphBuilder
from strands.multiagent.graph import Graph
from strands.telemetry.metrics import EventLoopMetrics
from strands.types.agent import AgentInput

from quorum.autonomy_hook import QuorumAutonomyGate
from quorum.decision_store import DecisionPolicyStore
from quorum.execution import ActionExecutionService
from quorum.executor_tools import build_executor_tools
from quorum.models import (
    ActionRequest,
    ExtractionEnvelope,
    ListenerDecision,
    PolicyDecision,
    RiskAssessment,
)
from quorum.orchestration import BedrockSettings, build_bedrock_model
from quorum.policy import assess_risk
from quorum.prompts import LEDGER_CURATOR_SYSTEM_PROMPT, LISTENER_SYSTEM_PROMPT

ACTION_REQUEST_STATE_KEY = "quorum_action_request"


def _action_request(invocation_state: object) -> ActionRequest:
    if not isinstance(invocation_state, dict):
        raise ValueError("decision graph requires invocation_state")
    raw_request = invocation_state.get(ACTION_REQUEST_STATE_KEY)
    if raw_request is None:
        raise ValueError(f"invocation_state.{ACTION_REQUEST_STATE_KEY} is required")
    return (
        raw_request
        if isinstance(raw_request, ActionRequest)
        else ActionRequest.model_validate(raw_request)
    )


def _agent_result(structured_output: RiskAssessment | PolicyDecision) -> AgentResult:
    return AgentResult(
        stop_reason="end_turn",
        message={
            "role": "assistant",
            "content": [{"text": structured_output.model_dump_json()}],
        },
        metrics=EventLoopMetrics(),
        state={},
        structured_output=structured_output,
    )


class DeterministicRiskNode:
    """Strands-compatible node that applies the fixed three-axis risk rubric."""

    async def invoke_async(self, prompt: AgentInput = None, **kwargs: Any) -> AgentResult:
        request = _action_request(kwargs.get("invocation_state"))
        return _agent_result(assess_risk(request))

    def __call__(self, prompt: AgentInput = None, **kwargs: Any) -> AgentResult:
        return asyncio.run(self.invoke_async(prompt, **kwargs))

    async def stream_async(
        self, prompt: AgentInput = None, **kwargs: Any
    ) -> AsyncIterator[dict[str, AgentResult]]:
        yield {"result": await self.invoke_async(prompt, **kwargs)}


class DeterministicQuorumRouterNode:
    """Strands-compatible node that persists autonomy, quorum, and budget routing."""

    def __init__(self, store: DecisionPolicyStore) -> None:
        self._store = store

    async def invoke_async(self, prompt: AgentInput = None, **kwargs: Any) -> AgentResult:
        request = _action_request(kwargs.get("invocation_state"))
        return _agent_result(self._store.decide(request))

    def __call__(self, prompt: AgentInput = None, **kwargs: Any) -> AgentResult:
        return asyncio.run(self.invoke_async(prompt, **kwargs))

    async def stream_async(
        self, prompt: AgentInput = None, **kwargs: Any
    ) -> AsyncIterator[dict[str, AgentResult]]:
        yield {"result": await self.invoke_async(prompt, **kwargs)}


@tool(name="execute_approved_action")
def execute_approved_action(organization_id: str, action_id: str) -> dict[str, str]:
    """Emit a non-mutating authorization receipt for the decision-layer milestone.

    Args:
        organization_id: Opaque organization identifier.
        action_id: Opaque action identifier.

    Returns:
        A receipt that explicitly identifies this as a dry execution boundary.
    """

    return {
        "organization_id": organization_id,
        "action_id": action_id,
        "status": "authorized_dry_run",
    }


def build_decision_graph(
    store: DecisionPolicyStore,
    *,
    model: Model | None = None,
    execution_service: ActionExecutionService | None = None,
) -> Graph:
    """Build the required five-node Graph with deterministic policy before execution."""

    active_model = model or build_bedrock_model(BedrockSettings.from_environment())
    listener = Agent(
        model=active_model,
        name="listener",
        description="Classifies messages for commitment processing.",
        system_prompt=LISTENER_SYSTEM_PROMPT,
        structured_output_model=ListenerDecision,
        callback_handler=None,
    )
    curator = Agent(
        model=active_model,
        name="ledger_curator",
        description="Extracts source-grounded commitments.",
        system_prompt=LEDGER_CURATOR_SYSTEM_PROMPT,
        structured_output_model=ExtractionEnvelope,
        callback_handler=None,
    )
    risk_appraiser = DeterministicRiskNode()
    quorum_router = DeterministicQuorumRouterNode(store)
    executor_tools: list[Any]
    if execution_service is not None:
        executor_tools = list(build_executor_tools(execution_service))
    else:
        executor_tools = [execute_approved_action]
    executor = Agent(
        model=active_model,
        name="executor",
        description="Calls only tools authorized by the persisted Quorum policy.",
        tools=executor_tools,
        hooks=[
            QuorumAutonomyGate(
                store,
                question_sender=(
                    execution_service.approval_notifier if execution_service is not None else None
                ),
            )
        ],
        callback_handler=None,
    )

    builder = GraphBuilder()
    builder.add_node(listener, "listener")
    builder.add_node(curator, "ledger_curator")
    builder.add_node(risk_appraiser, "risk_appraiser")
    builder.add_node(quorum_router, "quorum_router")
    builder.add_node(executor, "executor")
    builder.add_edge("listener", "ledger_curator")
    builder.add_edge("ledger_curator", "risk_appraiser")
    builder.add_edge("risk_appraiser", "quorum_router")
    builder.add_edge("quorum_router", "executor")
    builder.set_entry_point("listener")
    builder.set_graph_id("quorum-decision-graph-v1")
    builder.set_max_node_executions(5)
    builder.set_execution_timeout(90)
    return builder.build()


def decision_from_result(result: AgentResult) -> PolicyDecision:
    if not isinstance(result.structured_output, PolicyDecision):
        raise ValueError("quorum_router did not return PolicyDecision")
    return result.structured_output
