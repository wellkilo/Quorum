from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from strands import Agent

from quorum.models import ExtractionEnvelope, ListenerDecision
from quorum.orchestration import (
    BedrockSettings,
    OnlineConfigurationError,
    build_bedrock_model,
    build_ledger_graph,
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


if __name__ == "__main__":
    unittest.main()
