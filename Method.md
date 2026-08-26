# Quorum Method and SDK Contract

Status: phase-two executable contract based on `strands-agents==1.53.0`,
`pydantic==2.12.5`, `SQLAlchemy==2.0.52`, `Alembic==1.19.1`, and psycopg 3 for PostgreSQL. AgentCore
remains a verified design contract until credentials and deployment are available.

## Strands Graph

Implemented Graph imports and methods:

```python
from strands import Agent
from strands.models import BedrockModel
from strands.multiagent import GraphBuilder

from quorum.autonomy_hook import QuorumAutonomyGate
from quorum.decision_graph import DeterministicQuorumRouterNode, DeterministicRiskNode

model = BedrockModel(
    model_id=selected_model_id,
    region_name=explicit_region,
    temperature=0.0,
    top_p=0.1,
)
listener = Agent(
    model=model,
    structured_output_model=ListenerDecision,
)
ledger_curator = Agent(
    model=model,
    structured_output_model=ExtractionEnvelope,
)
risk_appraiser = DeterministicRiskNode()
quorum_router = DeterministicQuorumRouterNode(decision_store)
executor = Agent(
    model=model,
    tools=[execute_approved_action],
    hooks=[QuorumAutonomyGate(decision_store)],
)
builder = GraphBuilder()
builder.add_node(listener, "listener")
builder.add_node(ledger_curator, "ledger_curator")
builder.add_node(risk_appraiser, "risk_appraiser")
builder.add_node(quorum_router, "quorum_router")
builder.add_node(executor, "executor")
builder.add_edge("listener", "ledger_curator")
builder.add_edge("ledger_curator", "risk_appraiser")
builder.add_edge("risk_appraiser", "quorum_router")
builder.add_edge("quorum_router", "executor")
builder.set_entry_point("listener")
builder.set_max_node_executions(5)
builder.set_execution_timeout(90)
graph = builder.build()
```

The SDK propagates the original task and dependency result to the downstream node. The current
structured-output API is the `structured_output_model` argument on `Agent` or an invocation; the
older `Agent.structured_output()` helper is deprecated and is not used.

No candidate is persisted unless its `source_message_ref` equals the current event reference and its
`evidence_quote` is a verbatim substring of the current message. This invariant is implemented in
ordinary typed code after model extraction. The durable business store uses SQLite locally and
PostgreSQL in production; it stores structured ledger facts, not raw messages.

`ActionRequest` is supplied under `invocation_state["quorum_action_request"]`. The Risk Appraiser
and Quorum Router intentionally implement the Strands node invocation interface without using a
model. The router persists and returns a typed `PolicyDecision`. The Executor remains model-backed,
but its consequential tool boundary is independently enforced by the native hook. The current tool
returns `authorized_dry_run`; this milestone proves authorization, not an external side effect.

The five-node structure and deterministic policy nodes are tested without a model request. A live
Bedrock end-to-end invocation is not claimed until credentials and an explicit region are available.

## Business persistence

`DatabaseLedger` is the single SQLAlchemy implementation for SQLite and PostgreSQL. SQLite is the
zero-setup development and CI backend; production uses psycopg 3 with
`postgresql+psycopg://`. Deployed schema changes must run through Alembic and never through
`Base.metadata.create_all()`.

```text
Graph extraction
  -> deterministic evidence gate
  -> transaction
  -> processed_messages idempotency claim
  -> tenant-scoped commitment row lock/upsert
  -> append-only commitment_event
  -> commit
```

The initial migration creates four tables:

- `organizations`: opaque tenant identity;
- `processed_messages`: canonical-message fingerprint and idempotency claim, without raw text;
- `commitments`: current tenant-scoped business state with a monotonic version;
- `commitment_events`: immutable snapshots for create, update, and cancel operations.

Each audit event has composite foreign keys to both its tenant-scoped commitment and its processed
source-message record, so an audit entry cannot exist without both business and evidence anchors.

PostgreSQL uses `JSONB`, timezone-aware timestamps, composite tenant keys, and
`SELECT ... FOR UPDATE` for mutable targets. Message claims use
`INSERT ... ON CONFLICT DO NOTHING RETURNING`; an exact retry is a no-op, while the same identity
with a different fingerprint raises `IdempotencyConflictError`. The migration installs a database
trigger that rejects `UPDATE` and `DELETE` on `commitment_events`. SQLite receives equivalent
triggers so the local test contract matches the production invariant.

The second migration adds four policy tables:

- `autonomy_profiles`: per-organization, per-task-class earned autonomy state;
- `interrupt_budget_accounts`: lock targets for concurrent per-person budget routing;
- `action_decisions`: idempotent risk, quorum, timeout, and authorization outcomes;
- `interrupt_events`: append-only requested, approved, rejected, and expired evidence.

`DecisionPolicyStore.decide()` initializes and locks the autonomy profile and all ordered candidate
budget accounts before counting events and selecting a quorum. This makes the PostgreSQL routing
transaction resistant to concurrent budget overspend. `resolve()` locks the decision, accumulates
independent participant responses, and changes autonomy only once on a terminal approval or
rejection. `record_undo()` accepts only authorized, approved, or executed actions and is idempotent.
Irreversible actions cannot be undone. Timeout resolution runs under the same decision-row lock, so a
late response cannot authorize an expired action; only nonresponding participants receive an
`expired` audit event. The interrupt event table has database triggers rejecting update and delete
operations.

```bash
export QUORUM_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/quorum?sslmode=require'
uv run quorum-db upgrade
uv run quorum-db current
uv run quorum-db check
```

PostgreSQL holds authoritative business facts only. It does not replace the Runtime session ID, a
Strands session manager, or AgentCore Memory. Production connections set an application name, UTC,
a 15-second statement timeout, a 5-second lock timeout, health checks, and parameter redaction.
Credentials belong in a secret manager or deployment environment, never in source control.

## Bedrock model evaluation

The production model provider uses the standard boto3 credential chain. `QUORUM_AWS_REGION` is
mandatory; the code refuses to guess a region. `QUORUM_BEDROCK_MODEL_ID` may override the documented
default. The current machine has no AWS CLI, AWS config directory, AWS region variable, or AWS
credential variable, so no online score is claimed.

```bash
export QUORUM_AWS_REGION='<enabled-region>'
export QUORUM_BEDROCK_MODEL_ID='<enabled-model-id>'
uv run quorum-run-model-eval --output data/eval/predictions/bedrock_v1.jsonl
uv run quorum-eval \
  --predictions data/eval/predictions/bedrock_v1.jsonl \
  --predictor bedrock-v1 \
  --output reports/eval/bedrock_v1.json
```

## Strands Swarm

```python
from strands.multiagent import Swarm

ambiguity_swarm = Swarm([temporal_resolver, ownership_resolver])
```

Swarm is invoked only after deterministic extraction reports an ambiguity that blocks a ledger decision. Swarm output is advisory and must still pass the evidence invariant.

## Deterministic policy

The exact risk points are: reversible `0`, compensatable `1`, irreversible `3`; individual `0`, group
`1`, external `3`; no money `0`, budgeted `1`, unbudgeted `3`. Irreversible or unbudgeted actions, or
any total score of at least four, are high risk. Scores of two or three are medium; lower scores are
low.

High risk always requires two approvals. Medium risk requires one unless maximum autonomy and the
action is non-irreversible and money-free. Low risk requires one below `notify-and-undo` and zero at
or above it. The router selects the first minimum set with budget remaining; no person may receive
more than two requests in the preceding seven days. Insufficient budget yields `deferred_budget`.
All pending routes time out after 24 hours. Low risk defaults to execute-and-notify; medium and high
risk expire without action. Three consecutive approvals promote one level. Rejection and undo each
downgrade one level and reset the approval streak.

## Hook interrupt autonomy gate

```python
from strands.hooks import BeforeToolCallEvent, HookRegistry

class QuorumAutonomyGate:
    def register_hooks(self, registry: HookRegistry) -> None:
        for slot in range(10):
            registry.add_callback(BeforeToolCallEvent, self.approval_callback(slot))

    def approval_callback(self, slot):
        def authorize(event: BeforeToolCallEvent) -> None:
            decision = self.store.get_decision(
                event.tool_use["input"]["organization_id"],
                event.tool_use["input"]["action_id"],
            )
            response = event.interrupt(
                f"quorum-approval-{slot}",
                reason=interrupt_reason(decision, slot),
            )
            self.store.resolve(decision.organization_id, to_resolution(response))
        return authorize
```

Each callback can contribute one native interrupt, and Strands aggregates callbacks into the
multi-person interrupt set. On resume, each response is persisted and accumulated until the required
quorum is reached. The production implementation also fails closed for missing policy, mismatched
tools, rejected, expired, deferred, and undone actions. A plain model-generated approval is never
sufficient. Tests exercise both initial interruption and two-person resume through a real
`HookRegistry`.

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
