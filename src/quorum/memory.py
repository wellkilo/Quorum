"""AgentCore Memory session wiring with strict identity and region boundaries."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    PersistenceMode,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)

from quorum.models import OpaqueId


class MemoryConfigurationError(ValueError):
    """Raised when durable AgentCore Memory cannot be configured safely."""


@dataclass(frozen=True, slots=True)
class AgentCoreMemorySettings:
    memory_id: str
    region_name: str

    @classmethod
    def from_environment(cls) -> AgentCoreMemorySettings:
        memory_id = os.environ.get("QUORUM_AGENTCORE_MEMORY_ID", "").strip()
        region_name = os.environ.get("QUORUM_AWS_REGION", "").strip()
        if not memory_id:
            raise MemoryConfigurationError("QUORUM_AGENTCORE_MEMORY_ID is required")
        if not region_name:
            raise MemoryConfigurationError("QUORUM_AWS_REGION is required")
        return cls(memory_id=memory_id, region_name=region_name)


def provision_memory(
    *,
    region_name: str,
    name: str = "QuorumMemory",
    event_expiry_days: int = 90,
) -> dict[str, Any]:
    """Create or reuse the long-term memory required by Quorum."""

    if not region_name:
        raise MemoryConfigurationError("AgentCore Memory region is required")
    client = MemoryClient(region_name=region_name)
    return client.create_or_get_memory(
        name=name,
        description="Redacted organizational facts and session summaries for Quorum",
        event_expiry_days=event_expiry_days,
        strategies=[
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


def build_memory_session_manager(
    settings: AgentCoreMemorySettings,
    *,
    organization_id: OpaqueId,
    session_id: OpaqueId,
) -> AgentCoreMemorySessionManager:
    """Create one tenant-scoped Strands session manager per Runtime session."""

    config = AgentCoreMemoryConfig(
        memory_id=settings.memory_id,
        actor_id=organization_id,
        session_id=session_id,
        retrieval_config={
            "/facts/{actorId}/": RetrievalConfig(top_k=10, relevance_score=0.35),
            "/summaries/{actorId}/": RetrievalConfig(top_k=5, relevance_score=0.5),
        },
        batch_size=1,
        context_tag="organization_context",
        filter_restored_tool_context=True,
        default_metadata={
            "application": "quorum",
            "dataClassification": "redacted-or-synthetic",
        },
        persistence_mode=PersistenceMode.FULL,
        async_mode=True,
    )
    return AgentCoreMemorySessionManager(
        agentcore_memory_config=config,
        region_name=settings.region_name,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision Quorum's AgentCore Memory.")
    parser.add_argument("--region", required=True)
    parser.add_argument("--name", default="QuorumMemory")
    parser.add_argument("--event-expiry-days", type=int, default=90)
    args = parser.parse_args(argv)
    memory = provision_memory(
        region_name=args.region,
        name=args.name,
        event_expiry_days=args.event_expiry_days,
    )
    memory_id = memory.get("memoryId", memory.get("id"))
    if not isinstance(memory_id, str) or not memory_id:
        raise RuntimeError("AgentCore Memory response did not include a memory ID")
    print(f"memory_id={memory_id}")
    return 0
