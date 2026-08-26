from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from strands import Agent
from strands.session.session_manager import SessionManager

from quorum.models import ExtractionEnvelope, LedgerChangeSet, ListenerDecision
from quorum.orchestration import (
    BedrockSettings,
    OnlineConfigurationError,
    build_bedrock_model,
    build_ledger_graph,
    process_event_async,
)


class StrandsGraphTest(unittest.TestCase):
    def test_region_is_required_instead_of_guessed(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(OnlineConfigurationError, "QUORUM_AWS_REGION"),
        ):
            BedrockSettings.from_environment()

    def test_real_strands_graph_has_typed_two_node_path(self) -> None:
        model = build_bedrock_model(BedrockSettings(region_name="us-west-2"))

        graph = build_ledger_graph(model=model)

        self.assertEqual(set(graph.nodes), {"listener", "ledger_curator"})
        self.assertEqual(
            {(edge.from_node.node_id, edge.to_node.node_id) for edge in graph.edges},
            {("listener", "ledger_curator")},
        )
        self.assertEqual({node.node_id for node in graph.entry_points}, {"listener"})
        listener = graph.nodes["listener"].executor
        curator = graph.nodes["ledger_curator"].executor
        self.assertIsInstance(listener, Agent)
        self.assertIsInstance(curator, Agent)
        self.assertIs(listener._default_structured_output_model, ListenerDecision)
        self.assertIs(curator._default_structured_output_model, ExtractionEnvelope)

    def test_graph_uses_agentcore_session_manager_and_safe_trace_attributes(self) -> None:
        model = build_bedrock_model(BedrockSettings(region_name="us-west-2"))
        session_manager = MagicMock(spec=SessionManager)

        graph = build_ledger_graph(
            model=model,
            session_manager=session_manager,
            trace_attributes={"quorum.session_id": "session_opaque"},
        )

        self.assertIs(graph.session_manager, session_manager)
        self.assertEqual(graph.trace_attributes["quorum.session_id"], "session_opaque")


class AsyncLedgerProcessingTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_processing_awaits_graph_then_persists_typed_extraction(self) -> None:
        graph = MagicMock()
        graph.invoke_async = AsyncMock(return_value=object())
        ledger = MagicMock()
        expected = LedgerChangeSet()
        ledger.apply.return_value = expected
        event = MagicMock()
        extraction = ExtractionEnvelope()

        with (
            patch("quorum.orchestration.event_to_graph_task", return_value="canonical-task"),
            patch("quorum.orchestration.extraction_from_result", return_value=extraction),
        ):
            result = await process_event_async(graph, event, ledger)

        self.assertIs(result, expected)
        graph.invoke_async.assert_awaited_once_with("canonical-task")
        ledger.apply.assert_called_once_with(event, extraction)


if __name__ == "__main__":
    unittest.main()
