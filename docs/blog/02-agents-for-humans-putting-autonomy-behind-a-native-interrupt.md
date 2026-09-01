# Agents for Humans: Putting Agent Autonomy Behind a Native Interrupt

“Human in the loop” is easy to write in a diagram and surprisingly easy to bypass in code. If an
approval check lives in a route handler while the tool can be called somewhere else, the safety
claim is only a convention. Quorum takes a stricter approach: the autonomy policy is enforced at the
Strands tool boundary by a native interrupt.

Quorum is a coordination agent for small groups. Its product promise is to reduce interruptions, not
generate engagement. That makes the authorization path central to the product: the system must know
when it may stay quiet, when it must ask, whom it must ask, and when it must refuse to act.

## A deterministic spine for a probabilistic system

The main path is a Strands Graph with five nodes:

```text
Listener -> Ledger Curator -> Risk Appraiser -> Quorum Router -> Executor
```

The first two nodes interpret language and produce typed outputs. The remaining policy decisions do
not depend on model persuasion. The Risk Appraiser evaluates three explicit dimensions:
reversibility, impact radius, and money impact. The Quorum Router then combines that result with
earned autonomy, eligible decision owners, the 24-hour timeout rule, and each person's rolling
interrupt spend.

This division is deliberate. Models are useful for turning messy conversation into structured
candidates. They should not be able to rewrite the rules that decide whether an external side effect
is authorized.

Strands Swarm is similarly constrained. Quorum invokes a Swarm only when semantic ambiguity cannot
be resolved safely by the typed extraction path. Normal execution stays in the inspectable Graph.
This keeps the architecture honest: Swarm is a bounded exception, not a decorative label attached to
every request.

## The hook is the autonomy gate

The Executor registers a `BeforeToolCallEvent` hook. Immediately before a tool runs, the hook:

1. loads the policy decision by organization and action ID;
2. confirms that the approved tool name matches the requested tool;
3. compares a canonical fingerprint of the approved and requested arguments;
4. resolves any due timeout under the fixed risk policy;
5. either allows execution, cancels it, or calls `event.interrupt()` for the required participants.

For a two-person quorum, Quorum creates two native interrupts and resumes only after both responses
are bound to their original interrupt IDs. A rejection is final. A late high-risk response cannot
resurrect an expired decision. Slack delivery is useful for the participant experience, but it is
not the authority: even if sending the private question fails, the native interrupt still stops the
tool call.

The tests exercise missing policy, tool mismatch, argument mismatch, rejection, timeout, one- and
two-person approval, and successful resume. The behavior fails closed because every unexpected state
must result in no external action.

## Autonomy must be earned and reversible

The Autonomy Ladder has explicit transitions. Three consecutive approvals promote an action class
by one level. A rejection or undo resets the streak and lowers autonomy, never below ask-first.
High-risk actions still require two people even at the highest level.

The available execution tools are intentionally narrow:

- create a tentative Google Calendar event;
- create a Gmail draft, never send it;
- create a Google Form response request.

Each operation has an idempotency key and an argument fingerprint. Successful execution persists an
opaque provider resource ID and produces one Slack group receipt with Open and Undo actions. The undo
token is HMAC-signed, expires after 24 hours, is stored only as a digest, and is consumed atomically
before deletion. Ambiguous provider responses are recorded as `uncertain` and are not retried
automatically, avoiding duplicate side effects.

## Four kinds of state, four different jobs

Quorum does not use one database label for everything.

- **PostgreSQL** is the authority for commitments, policy decisions, interruption spend, execution
  receipts, and append-only audit events.
- **Strands session management** persists Graph and conversation state for a session.
- **AgentCore Memory** is the target for durable organization facts and summaries.
- **AgentCore Runtime** provides hosted invocation and runtime session identity.

The Runtime session ID is not treated as durable memory. PostgreSQL is not presented as AgentCore
Memory. This separation is visible in the architecture because it prevents a common demo shortcut
from becoming a production ambiguity.

AgentCore Gateway is the target MCP boundary for the three tools. The implementation discovers the
IAM-authenticated Gateway tools, rejects duplicates or missing names, validates their typed inputs,
and dispatches them through a Lambda adapter to the same execution service used locally.

OpenTelemetry follows the same boundary-first design. Quorum allow-lists a small set of correlation
attributes and enables Strands sensitive-attribute redaction. Prompts, raw messages, tool arguments,
and provider payloads are not trace attributes. A short-lived Runtime run retrieved exactly one
managed synthetic probe span, matched its returned identifiers, and reported zero model, Memory,
Gateway, or external side-effect calls. The [evidence record](../evidence/agentcore-observability-2026-09-01.md)
documents the successful run and cleanup without presenting it as a production trace.

## What is implemented, and what is not claimed

The Graph, deterministic nodes, native interrupt behavior, PostgreSQL-compatible schema, Runtime
application, Memory session-manager integration, Gateway schemas and client, reversible provider
adapters, and safe trace configuration are implemented and tested locally. The reviewed Runtime
CodeZip has also completed a short-lived GitHub OIDC deployment: READY, HTTP 503 at the closed model
gate, then automatic cleanup. A separate zero-model run brought Memory to `ACTIVE` with both
configured strategies and an IAM-authenticated Gateway to `READY`, verified the exact MCP tool list,
and then cleaned every resource. It created zero Memory events and made zero Gateway tool calls. Real
Slack and Google Workspace calls are not claimed without credentials.

That distinction is encoded in the architecture image: Runtime, Memory, Gateway, and the single
synthetic managed span are marked as short-lived verified evidence followed by cleanup. The public
demo at <https://wellkilo.github.io/Quorum/> is a static, visibly synthetic replay, not a disguised
Runtime. The source, tests, and diagram are available at <https://github.com/wellkilo/Quorum>.

For Quorum, autonomy is not the absence of a human. It is a specific, earned, testable authority
state, enforced at the last responsible moment before an action leaves the system.
