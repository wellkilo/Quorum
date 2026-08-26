# Quorum Method and SDK Contract

Status: implementation contract based on current official Strands and Amazon Bedrock AgentCore APIs. Exact dependency versions will be pinned when the first executable agent slice is added.

## Strands Graph

Planned Python imports and methods:

```python
from strands import Agent
from strands.multiagent import GraphBuilder

builder = GraphBuilder()
builder.add_node(listener, "listener")
builder.add_node(ledger_curator, "ledger_curator")
builder.add_node(risk_appraiser, "risk_appraiser")
builder.add_node(quorum_router, "quorum_router")
builder.add_node(executor, "executor")
builder.add_edge("listener", "ledger_curator")
builder.add_edge("ledger_curator", "risk_appraiser")
builder.add_edge("risk_appraiser", "quorum_router")
builder.add_edge("quorum_router", "executor", condition=may_execute)
builder.set_entry_point("listener")
builder.set_max_node_executions(10)
builder.set_execution_timeout(60)
graph = builder.build()
```

No ledger node may return a commitment without `source_message_ref`. The graph must validate this invariant outside model reasoning.

## Strands Swarm

```python
from strands.multiagent import Swarm

ambiguity_swarm = Swarm([temporal_resolver, ownership_resolver])
```

Swarm is invoked only after deterministic extraction reports an ambiguity that blocks a ledger decision. Swarm output is advisory and must still pass the evidence invariant.

## Hook interrupt autonomy gate

```python
from strands.hooks import BeforeToolCallEvent, HookProvider, HookRegistry

class AutonomyGate(HookProvider):
    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(BeforeToolCallEvent, self.authorize)

    def authorize(self, event: BeforeToolCallEvent) -> None:
        decision = evaluate_policy(event.tool_use)
        if decision.requires_human:
            answer = event.interrupt(
                "quorum-approval",
                reason=decision.to_interrupt_reason(),
            )
            if not answer.get("approved"):
                event.cancel_tool = "Approval denied or expired"
```

Policy evaluation must be deterministic and include reversibility, impact radius, money, autonomy level, quorum, timeout default, and the person's rolling interrupt spend. A plain model-generated approval is never sufficient.

## AgentCore Runtime

```python
from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

@app.entrypoint
async def handler(request):
    validated = RuntimeRequest.from_mapping(request)
    async for event in graph.stream_async(validated.to_graph_input()):
        yield event

app.run()
```

The caller supplies `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`. Runtime session identity is not used as a substitute for graph-state persistence.

## Session persistence

Local development may begin with `FileSessionManager`; the deployed path must use the AgentCore Memory Strands integration or another verified durable manager. Session state stores orchestration progress and pending interrupts, not raw unredacted Slack payloads.

## AgentCore Memory

Planned integration:

```python
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
```

Namespaces must separate organization, actor, and session scope. Only redacted durable facts, autonomy history, and approved summaries may enter long-term memory.

## AgentCore Gateway

Gateway exposes narrow MCP tools rather than a single unrestricted executor. Planned tool families:

- `calendar.create_tentative_event` / `calendar.cancel_event`;
- `email.create_draft` / `email.discard_draft`;
- `form.create_response_request` / `form.close_response_request`.

Every mutating tool returns an `action_id`, reversibility class, undo deadline, and provider receipt. Tool targets will be implemented against real APIs only after credentials and scopes are available.

## OpenTelemetry

AgentCore Observability uses ADOT/OpenTelemetry. Required correlation attributes include opaque organization ID, runtime session ID, graph node, policy outcome, interruption cost, and action ID. Raw text and PII are forbidden attributes.

## Slack Web API methods

The initial real channel requires these Slack capabilities:

- Events API callback for `message.channels`;
- `chat.postMessage` for group receipts and weekly summary;
- `conversations.open` followed by `chat.postMessage` for a private question;
- Block Kit interactive action callback for approve, reject, edit, and undo.

Exact OAuth scopes and Slack app manifest will be recorded and tested before the integration is claimed as working.
