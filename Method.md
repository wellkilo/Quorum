# Quorum Method and SDK Contract

Status: phase-four executable contract based on `strands-agents==1.53.0`,
`pydantic==2.12.5`, `SQLAlchemy==2.0.52`, `Alembic==1.19.1`,
`google-api-python-client==2.199.0`, `google-auth==2.57.0`, `slack-sdk==3.43.0`, and psycopg 3 for
PostgreSQL, plus `bedrock-agentcore==1.22.0` and `mcp-proxy-for-aws==1.6.4`. AgentCore adapters and
local HTTP surfaces are executable and tested; cloud provisioning and credentialed calls remain
unverified until AWS credentials and deployment resources are available.

## Strands Graph

Implemented Graph imports and methods:

```python
from strands import Agent
from strands.models import BedrockModel
from strands.multiagent import GraphBuilder

from quorum.autonomy_hook import QuorumAutonomyGate
from quorum.decision_graph import DeterministicQuorumRouterNode, DeterministicRiskNode
from quorum.executor_tools import build_executor_tools

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
    tools=build_executor_tools(execution_service),
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
but its consequential tool boundary is independently enforced by the native hook. When an
`ActionExecutionService` is supplied, the Executor registers only the three reversible phase-three
tools. A dry-run tool remains available only when the graph is deliberately built without that
service for local authorization tests.

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

The third migration binds decisions to canonical action-argument fingerprints and adds:

- `action_executions`: one idempotent provider execution per tenant-scoped action;
- `undo_tokens`: only SHA-256 token digests, expiry, and atomic consumption state;
- `execution_events`: append-only started, executed, uncertain, receipt, and undo transitions.

Action arguments are never persisted. `ExecutionStore.start()` locks the policy decision and checks
the tool, normalized argument fingerprint, executable state, provider identity, and reversibility
before a provider adapter is called. Exact successful retries return the prior receipt. Failed or
uncertain executions are not retried automatically.

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
sufficient. The hook also hashes the actual tool input after excluding only `organization_id` and
`action_id`; a model cannot alter a recipient, message, title, date, or question after approval.
Tests exercise argument tampering, initial interruption, and two-person resume through a real
`HookRegistry`.

## Google Workspace execution

The adapters use Application Default Credentials with only these requested scopes:

- `calendar.events` for tentative event create/delete;
- `gmail.compose` for draft create/delete, never send;
- `forms.body` for form create/configure;
- `drive.file` for deleting the form created by Quorum.

The Calendar adapter calls `events().insert(calendarId="primary", sendUpdates="none", ...)` and
marks the event tentative. Gmail builds an RFC 2822 plain-text message, base64url encodes it, and
calls `users().drafts().create(userId="me", ...)`. Forms calls `forms().create(...)`, then
`forms().batchUpdate(...)`; if configuration fails, it attempts a compensating Drive delete. Tests
replace only the HTTP boundary and assert these exact SDK method names and payloads.

Provider failures expose stable codes without credentials or content. Transport failures, 5xx
responses, malformed responses, and successful-looking responses without a resource ID are marked
outcome-uncertain. Quorum does not automatically retry those calls because doing so could duplicate
an external side effect.

## Signed undo

`UndoTokenSigner` signs canonical organization, action, expiry, and version claims with HMAC-SHA256.
The secret must contain at least 32 bytes. Tokens expire after 24 hours; only a SHA-256 digest is
stored. `ExecutionStore.reserve_undo()` locks and consumes the token and moves the execution to
`undoing` before calling the relevant Calendar, Gmail, or Drive delete operation. Success records an
append-only `undone` event and lowers autonomy one level. A failed compensation is recorded as
`undo_failed` and is not disguised as success.

## AgentCore Runtime

```python
from quorum.runtime import app

app.run()
```

`src/quorum/runtime.py` creates one `BedrockAgentCoreApp`. Its native `/invocations` entrypoint
validates `RuntimeInvocation`, checks that the action and invocation organization IDs match, and
requires `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`. The ID follows the current Runtime contract:
33–256 characters, alphanumeric first, then alphanumerics, hyphens, or underscores. The same app
also hosts Slack Events, undo confirmation/execution, and the synthetic replay, so Quorum does not
introduce another product surface.

The production model path is fail-closed. `BedrockSettings.from_environment()` rejects Runtime,
Slack processing, and model-evaluation work unless `QUORUM_BEDROCK_ENABLED=true` is set explicitly.
Runtime returns HTTP `503` before constructing Memory, Gateway, or Bedrock clients when the gate is
closed. Slack ingress performs the same preflight before accepting work for background processing.
`QUORUM_BEDROCK_MAX_TOKENS` defaults to 384 and is constrained to 64–1024. It limits output per call,
but it is not described as an account-level cost cap.

The anonymous GitHub Pages build publishes the same HTML, CSS, and JavaScript under the repository
subpath. Relative asset URLs preserve both Runtime-root and Pages-subpath delivery. On
`wellkilo.github.io`, the replay reads a versioned static synthetic fixture; elsewhere it calls the
Runtime POST endpoint. CI validates the fixture provenance and an executable test prevents it from
drifting from the Runtime replay contract.

The manual GitHub Actions deployment uses OIDC-scoped short-lived AWS credentials. It builds a
Python 3.13 Linux arm64 CodeZip, stores it in a private one-day-lifecycle S3 bucket, and creates or
updates only `QuorumRuntime`. The Runtime role has no model allow statement and explicitly denies
`bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`. A cleanup operation removes
the Runtime, its default Runtime endpoint, archive, and bucket; the workflow also attempts cleanup
after a failed AWS deployment step. The temporary SQLite path under `/tmp` is suitable only for
this stateless deployment check and is not documented as durable business persistence.
The archive places `agentcore_main.py` at its root as `main.py`, matching the direct-code Runtime
contract, while the application implementation remains in `quorum.runtime`.

The production invoker opens an IAM-authenticated Gateway MCP client, injects its three tools into
the five-node Graph, passes `ActionRequest` through `invocation_state`, and converts submitted
interrupt responses into Strands `InterruptResponseContent`. Runtime session identity is not used as
a substitute for business persistence.

## Session persistence

Both the two-node ingestion Graph and five-node decision Graph receive a Strands `SessionManager`. In
the production constructors this is `AgentCoreMemorySessionManager`, keyed by the opaque organization
as `actor_id` and the validated Runtime/message identity as `session_id`. Session state stores
orchestration progress and pending interrupts, not authoritative business rows.

## AgentCore Memory

Implemented integration:

```python
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
```

`AgentCoreMemoryConfig` uses full persistence, asynchronous writes, restored-tool-context filtering,
and retrieval from `/facts/{actorId}/` and `/summaries/{actorId}/`. The actual Memory resource is
provisioned with semantic facts at `/facts/{actorId}/` and summaries at
`/summaries/{actorId}/{sessionId}/`:

```bash
uv run quorum-provision-memory --region '<aws-region>'
```

The command uses `MemoryClient.create_or_get_memory`; its output populates
`QUORUM_AGENTCORE_MEMORY_ID`. Only redacted or synthetic model traffic may reach this integration.

## AgentCore Gateway

Gateway exposes the same three narrow contracts rather than a single unrestricted executor:

- `calendar_create_tentative_event`;
- `gmail_create_draft`;
- `forms_create_response_request`.

`gateway_tool_definitions()` converts the Pydantic inputs into AgentCore Gateway's recursive inline
Lambda schema. `gateway_lambda_handler()` reads the selected tool from
`context.client_context.custom["bedrockAgentCoreToolName"]`, rejects all other tools, validates the
flat argument object, and delegates to `ActionExecutionService`. `gateway_executor_tools()` connects
with `aws_iam_streamablehttp_client` and renames only the three allowed remote tools to their stable
local names. The existing `QuorumAutonomyGate` remains attached to the Executor around those MCP
tools.

```bash
uv run quorum-provision-gateway \
  --region '<aws-region>' \
  --role-arn '<gateway-service-role-arn>' \
  --lambda-arn '<execution-lambda-arn>'
```

This helper creates an `AWS_IAM` MCP Gateway and a `GATEWAY_IAM_ROLE` Lambda target. Packaging the
Lambda and creating its role are explicit deployment prerequisites, not hidden behavior.

## OpenTelemetry

AgentCore Observability uses managed ADOT/OpenTelemetry. `safe_trace_attributes()` enforces a closed
allowlist of bounded scalar correlation attributes: opaque organization/session/action/replay IDs,
graph node, policy outcome, interruption count, and data classification.

Strands is configured with `gen_ai_unredacted_attributes=` before an application is built. In
`strands-agents==1.53.0`, the presence of this token with an empty allowlist redacts sensitive prompt,
system-instruction, model-output, tool-argument, and tool-result attributes. Quorum never adds raw
messages or provider payloads as custom span attributes.

## Slack Web API methods

The outbound adapter implements these Slack Web API methods:

- `chat.postMessage` for the one-line group receipt;
- `conversations.open` followed by `chat.postMessage` for a private question;
- Block Kit URL buttons for Open and Undo.

The required outbound bot scopes are `chat:write` and `im:write`. Slack Events ingress verifies the
exact raw body with Slack's v0 HMAC scheme and a five-minute replay window, rejects malformed event
timestamps, ignores bots and subtypes, pseudonymizes workspace/channel/user/message IDs, and redacts
mentions, email addresses, phone numbers, and IPv4 addresses before Graph invocation. The ASGI route
returns the 200 acknowledgement before background Graph processing. A live credentialed workspace
test and the Sunday summary are still outstanding.
