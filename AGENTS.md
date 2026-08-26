# Quorum Agent Development Contract

## Objective

Maximize the judging score of Quorum for the AWS Agents for Humans Hackathon without overstating evidence. Quorum remains a Good Neighbor Agent centered on the Commitment Ledger, Autonomy Ladder, Minimum-Quorum Routing, and Interrupt Budget. Do not replace the concept or any of these four mechanisms without explicit owner approval.

## External communication

- Repository content, code comments, demo copy, blog drafts, diagrams, and submission material must be English.
- Never describe synthetic data as real.
- Never invent a user, organization, quotation, metric, deployment, test result, or AWS capability.
- Prefer explicit limitations over optimistic claims.

## Scope control

- The real-time channel is Slack.
- The product has exactly three interaction surfaces: group receipt with undo, one private question, and weekly summary.
- The deterministic Strands Graph is the production path. Swarm is only for bounded semantic ambiguity.
- After the six task classes are frozen, additions require a written judging-criterion benefit and owner approval.
- After the submission freeze, only defect fixes are allowed.

## Required architecture

```text
Slack event
  -> signature verification
  -> canonical PII-safe event
  -> Listener
  -> Ledger Curator with mandatory source evidence
  -> Risk Appraiser
  -> Minimum-Quorum Router
  -> hook interrupt or Executor
  -> AgentCore Gateway MCP tool
  -> receipt and undo record
  -> session state, AgentCore Memory, and OTEL trace
```

Runtime session identity, Strands session persistence, AgentCore long-term memory, and the PostgreSQL business-fact ledger must remain separate in code and documentation.

## Quality gates

- Core domain objects require explicit types.
- Consequential tool calls require deterministic policy evaluation before model output can trigger them.
- Every commitment requires a source-message reference.
- Critical paths and boundaries require tests.
- Run formatting, linting, type checks, unit tests, and the focused demo smoke test before delivery.
- Maintain `API.md` and `Method.md` whenever an external contract changes.
- Log IDs and counters only; never log raw messages, tokens, email addresses, phone numbers, names, or free-form model prompts.

## Data boundaries

- `data/raw`, `data/private`, and `data/redacted` are never committed.
- Real data requires documented consent and local redaction before use.
- Public fixtures use `data_classification: synthetic`.
- Real and synthetic metrics must never be combined.
- A quotation cannot be published without explicit approval of its final wording.
- SQLite is the local business store; PostgreSQL with Alembic migrations is the production business store. Neither is a substitute for AgentCore Memory.

## Definition of done for a feature

1. The behavior improves a named judging criterion.
2. The real SDK, API, or MCP contract is documented.
3. The happy path and critical failure path have executable tests.
4. The behavior can be shown in the five-minute demo.
5. The README claim is no stronger than the observed evidence.
