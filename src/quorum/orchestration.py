"""Real Strands Graph construction and commitment-ledger execution."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from strands import Agent
from strands.agent.agent_result import AgentResult
from strands.models import BedrockModel
from strands.models.model import Model
from strands.multiagent import GraphBuilder
from strands.multiagent.graph import Graph, GraphResult, GraphState

from quorum.ledger import LedgerRepository
from quorum.models import (
    CanonicalMessageEvent,
    ExtractionEnvelope,
    LedgerChangeSet,
    ListenerDecision,
)
from quorum.prompts import LEDGER_CURATOR_SYSTEM_PROMPT, LISTENER_SYSTEM_PROMPT

DEFAULT_MODEL_ID = "global.amazon.nova-2-lite-v1:0"


class OnlineConfigurationError(RuntimeError):
    """Raised when an online Bedrock graph cannot be configured safely."""


class GraphOutputError(RuntimeError):
    """Raised when a graph node does not return its declared structured type."""


@dataclass(frozen=True, slots=True)
class BedrockSettings:
    region_name: str
    model_id: str = DEFAULT_MODEL_ID

    @classmethod
    def from_environment(cls) -> BedrockSettings:
        region = os.environ.get("QUORUM_AWS_REGION", "").strip()
        if not region:
            raise OnlineConfigurationError(
                "QUORUM_AWS_REGION is required; Quorum never guesses an AWS region"
            )
        model_id = os.environ.get("QUORUM_BEDROCK_MODEL_ID", DEFAULT_MODEL_ID).strip()
        if not model_id:
            raise OnlineConfigurationError("QUORUM_BEDROCK_MODEL_ID cannot be empty")
        return cls(region_name=region, model_id=model_id)


def build_bedrock_model(settings: BedrockSettings) -> BedrockModel:
    """Construct the production Bedrock provider using the standard boto3 credential chain."""

    return BedrockModel(
        model_id=settings.model_id,
        region_name=settings.region_name,
        temperature=0.0,
        top_p=0.1,
    )


def _listener_decision_from_state(state: GraphState) -> ListenerDecision | None:
    node_result = state.results.get("listener")
    if node_result is None or not isinstance(node_result.result, AgentResult):
        return None
    structured = node_result.result.structured_output
    return structured if isinstance(structured, ListenerDecision) else None


def should_curate(state: GraphState) -> bool:
    """Traverse to the curator only for commitments, mutations, or ambiguity."""

    decision = _listener_decision_from_state(state)
    return decision is not None and decision.eligible_for_ledger


def build_ledger_graph(*, model: Model | None = None) -> Graph:
    """Build Listener -> Ledger Curator using the installed Strands SDK."""

    active_model = model or build_bedrock_model(BedrockSettings.from_environment())
    listener = Agent(
        model=active_model,
        name="listener",
        description="Classifies explicit commitment intent without inferring promises.",
        system_prompt=LISTENER_SYSTEM_PROMPT,
        structured_output_model=ListenerDecision,
        callback_handler=None,
    )
    curator = Agent(
        model=active_model,
        name="ledger_curator",
        description="Extracts evidence-grounded commitment candidates.",
        system_prompt=LEDGER_CURATOR_SYSTEM_PROMPT,
        structured_output_model=ExtractionEnvelope,
        callback_handler=None,
    )

    builder = GraphBuilder()
    builder.add_node(listener, "listener")
    builder.add_node(curator, "ledger_curator")
    builder.add_edge("listener", "ledger_curator", condition=should_curate)
    builder.set_entry_point("listener")
    builder.set_max_node_executions(2)
    builder.set_execution_timeout(60)
    return builder.build()


def event_to_graph_task(event: CanonicalMessageEvent) -> str:
    """Serialize a validated event without adding hidden context."""

    return json.dumps(event.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def extraction_from_result(result: GraphResult) -> ExtractionEnvelope:
    """Read the curator's typed result from a completed Strands graph."""

    node_result = result.results.get("ledger_curator")
    if node_result is None:
        listener_result = result.results.get("listener")
        if listener_result is not None and isinstance(listener_result.result, AgentResult):
            decision = listener_result.result.structured_output
            if isinstance(decision, ListenerDecision) and not decision.eligible_for_ledger:
                return ExtractionEnvelope()
        raise GraphOutputError("ledger_curator did not produce a node result")
    if isinstance(node_result.result, Exception):
        raise GraphOutputError("ledger_curator failed") from node_result.result
    if not isinstance(node_result.result, AgentResult):
        raise GraphOutputError("ledger_curator returned a non-agent result")
    structured = node_result.result.structured_output
    if not isinstance(structured, ExtractionEnvelope):
        raise GraphOutputError("ledger_curator did not return ExtractionEnvelope")
    return structured


def process_event(
    graph: Graph, event: CanonicalMessageEvent, ledger: LedgerRepository
) -> LedgerChangeSet:
    """Run the online graph, then enforce evidence invariants before persistence."""

    graph_result = graph(event_to_graph_task(event))
    extraction = extraction_from_result(graph_result)
    return ledger.apply(event, extraction)
