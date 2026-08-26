from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from quorum.memory import (
    AgentCoreMemorySettings,
    MemoryConfigurationError,
    build_memory_session_manager,
    provision_memory,
)


class AgentCoreMemoryTest(unittest.TestCase):
    def test_environment_requires_explicit_memory_and_region(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(MemoryConfigurationError, "MEMORY_ID"),
        ):
            AgentCoreMemorySettings.from_environment()

        with (
            patch.dict(os.environ, {"QUORUM_AGENTCORE_MEMORY_ID": "mem-123"}, clear=True),
            self.assertRaisesRegex(MemoryConfigurationError, "AWS_REGION"),
        ):
            AgentCoreMemorySettings.from_environment()

    def test_manager_maps_organization_to_actor_and_runtime_id_to_session(self) -> None:
        captured: dict[str, object] = {}

        class FakeSessionManager:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        with patch("quorum.memory.AgentCoreMemorySessionManager", FakeSessionManager):
            manager = build_memory_session_manager(
                AgentCoreMemorySettings(memory_id="mem-123", region_name="us-west-2"),
                organization_id="org_opaque",
                session_id="session_opaque",
            )

        self.assertIsInstance(manager, FakeSessionManager)
        self.assertEqual(captured["region_name"], "us-west-2")
        config = captured["agentcore_memory_config"]
        self.assertEqual(config.memory_id, "mem-123")
        self.assertEqual(config.actor_id, "org_opaque")
        self.assertEqual(config.session_id, "session_opaque")
        self.assertEqual(
            set(config.retrieval_config),
            {"/facts/{actorId}/", "/summaries/{actorId}/"},
        )
        self.assertTrue(config.filter_restored_tool_context)
        self.assertTrue(config.async_mode)

    def test_provisioning_creates_fact_and_session_summary_strategies(self) -> None:
        client = unittest.mock.MagicMock()
        client.create_or_get_memory.return_value = {"memoryId": "memory-123"}

        with patch("quorum.memory.MemoryClient", return_value=client) as factory:
            result = provision_memory(region_name="us-west-2")

        factory.assert_called_once_with(region_name="us-west-2")
        self.assertEqual(result["memoryId"], "memory-123")
        request = client.create_or_get_memory.call_args.kwargs
        self.assertEqual(request["event_expiry_days"], 90)
        self.assertEqual(
            request["strategies"],
            [
                {
                    "semanticMemoryStrategy": {
                        "name": "QuorumFacts",
                        "namespaceTemplates": ["/facts/{actorId}/"],
                    }
                },
                {
                    "summaryMemoryStrategy": {
                        "name": "QuorumSummaries",
                        "namespaceTemplates": ["/summaries/{actorId}/{sessionId}/"],
                    }
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
