from __future__ import annotations

import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from alembic import command
from alembic.config import Config
from strands import Agent

from quorum.database import DatabaseSettings, create_database_engine
from quorum.decision_graph import (
    ACTION_REQUEST_STATE_KEY,
    DeterministicQuorumRouterNode,
    DeterministicRiskNode,
    build_decision_graph,
    decision_from_result,
)
from quorum.decision_store import DecisionPolicyStore
from quorum.execution import ActionExecutionService
from quorum.models import (
    ActionRequest,
    DecisionStatus,
    ImpactRadius,
    MoneyImpact,
    Reversibility,
    RiskAssessment,
    RiskTier,
    TaskClass,
)
from quorum.orchestration import BedrockSettings, build_bedrock_model

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_request() -> ActionRequest:
    return ActionRequest(
        action_id="action_graph",
        organization_id="org_graph",
        requested_by_id="person_requester",
        action_class=TaskClass.EVENT_DECISION,
        tool_name="execute_approved_action",
        summary="Create a tentative planning event",
        reversibility=Reversibility.REVERSIBLE,
        impact_radius=ImpactRadius.INDIVIDUAL,
        money_impact=MoneyImpact.NONE,
        candidate_decider_ids=["person_a"],
        action_arguments={},
        requested_at=datetime(2026, 8, 26, 10, tzinfo=UTC),
    )


class DecisionGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        path = Path(self.temp_dir.name) / "graph.sqlite3"
        self.engine = create_database_engine(DatabaseSettings(url=f"sqlite+pysqlite:///{path}"))
        config = Config(PROJECT_ROOT / "alembic.ini")
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        self.store = DecisionPolicyStore(self.engine)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_five_node_graph_uses_deterministic_policy_nodes_and_hooked_executor(self) -> None:
        model = build_bedrock_model(BedrockSettings(region_name="us-west-2"))

        graph = build_decision_graph(self.store, model=model)

        self.assertEqual(
            set(graph.nodes),
            {
                "listener",
                "ledger_curator",
                "risk_appraiser",
                "quorum_router",
                "executor",
            },
        )
        self.assertEqual(
            {(edge.from_node.node_id, edge.to_node.node_id) for edge in graph.edges},
            {
                ("listener", "ledger_curator"),
                ("ledger_curator", "risk_appraiser"),
                ("risk_appraiser", "quorum_router"),
                ("quorum_router", "executor"),
            },
        )
        self.assertIsInstance(graph.nodes["listener"].executor, Agent)
        self.assertIsInstance(graph.nodes["ledger_curator"].executor, Agent)
        self.assertIsInstance(graph.nodes["risk_appraiser"].executor, DeterministicRiskNode)
        self.assertIsInstance(graph.nodes["quorum_router"].executor, DeterministicQuorumRouterNode)
        executor = graph.nodes["executor"].executor
        self.assertIsInstance(executor, Agent)
        self.assertTrue(executor.hooks.has_callbacks())

    def test_deterministic_nodes_emit_typed_results_without_model_call(self) -> None:
        invocation_state = {ACTION_REQUEST_STATE_KEY: make_request()}

        risk_result = DeterministicRiskNode()(invocation_state=invocation_state)
        decision_result = DeterministicQuorumRouterNode(self.store)(
            invocation_state=invocation_state
        )

        self.assertIsInstance(risk_result.structured_output, RiskAssessment)
        self.assertIs(risk_result.structured_output.tier, RiskTier.LOW)
        decision = decision_from_result(decision_result)
        self.assertIs(decision.status, DecisionStatus.AWAITING_APPROVAL)
        self.assertEqual(decision.selected_decider_ids, ["person_a"])

    def test_executor_registers_only_the_three_reversible_stage_three_tools(self) -> None:
        model = build_bedrock_model(BedrockSettings(region_name="us-west-2"))
        execution_service = MagicMock(spec=ActionExecutionService)

        graph = build_decision_graph(
            self.store,
            model=model,
            execution_service=execution_service,
        )

        executor = graph.nodes["executor"].executor
        self.assertIsInstance(executor, Agent)
        self.assertEqual(
            executor.tool_names,
            [
                "calendar_create_tentative_event",
                "gmail_create_draft",
                "forms_create_response_request",
            ],
        )
        self.assertTrue(executor.hooks.has_callbacks())


if __name__ == "__main__":
    unittest.main()
