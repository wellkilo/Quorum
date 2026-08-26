# Quorum

> A group coordination agent whose primary success metric is how rarely it interrupts people.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Hackathon](https://img.shields.io/badge/AWS-Agents%20for%20Humans-ff9900)](https://agentsforhumans.devpost.com/)
[![Track](https://img.shields.io/badge/Track-Good%20Neighbor%20Agents-2f855a)](https://agentsforhumans.devpost.com/)

Quorum coordinates the routine decisions that exhaust small communities. It listens where the group already talks, turns commitments into an evidence-linked ledger, acts autonomously only within earned boundaries, and asks the minimum number of people required for a safe decision.

Its product promise is deliberately unusual: **the best coordination agent is the one you barely notice.**

## Status

Quorum is an active entry in the 2026 AWS Agents for Humans Hackathon. The current executable slice includes a typed commitment ledger, the complete five-node Strands Graph structure, a deterministic risk and routing policy, a Strands-native hook interrupt autonomy gate, transactional SQLite/PostgreSQL persistence, Alembic migrations, and a 50-case synthetic evaluation suite. The Slack and AWS production paths are not yet claimed as deployed.

No real organization data, user quote, impact result, live-demo URL, or Bedrock model score is claimed at this stage. Every current evaluation case is labeled `synthetic` in its metadata.

## Run the verified local slice

Python 3.11–3.13 and [`uv`](https://docs.astral.sh/uv/) are required.

```bash
uv sync --extra dev --extra postgres
uv run quorum-db upgrade
uv run quorum-db check
uv run quorum-validate-gold
uv run python -m unittest discover -s tests -v
uv run ruff format --check src migrations tests scripts
uv run ruff check src migrations tests scripts
uv run mypy src/quorum
```

The Graph constructor uses the installed `strands-agents==1.53.0` package. It can be built without
making a model request. Online extraction requires an explicitly selected AWS region and valid
credentials through the standard boto3 credential chain; Quorum does not guess a region.

## The four mechanisms

1. **Commitment Ledger** — every extracted commitment keeps a source-message reference. If Quorum cannot point to evidence, it does not write the commitment.
2. **Autonomy Ladder** — low-risk actions graduate from ask-first to notify-and-undo only after repeated approvals. Rejection or reversal lowers autonomy.
3. **Minimum-Quorum Routing** — decisions go only to the smallest sufficient set of accountable people, with a visible timeout and default.
4. **Interrupt Budget** — each person receives at most two decision requests per rolling week by default. This is Quorum's primary product metric, not a settings toggle.

## One surface, three interactions

Quorum does not ask a community to adopt another application. Its complete interaction model is:

- one line in the group after an action, including an undo link;
- one direct message when a real decision cannot be made safely;
- one weekly summary showing outcomes and interruption spend.

## Architecture

```mermaid
flowchart LR
    Slack[Slack Events API] --> Listener[Listener]
    Listener --> Ledger[Ledger Curator]
    Ledger --> BusinessDB[(Commitment Ledger DB)]
    Ledger --> Risk[Risk Appraiser]
    Risk --> Router[Minimum-Quorum Router]
    Router --> Gate{Autonomy Hook}
    Gate -->|interrupt| DM[One private question]
    Gate -->|within policy| Executor[Executor]
    DM --> Executor
    Executor --> Gateway[AgentCore Gateway / MCP]
    Gateway --> Tools[Calendar, email, and form tools]
    Executor --> Receipt[Group receipt + undo]

    Listener <--> Session[Strands Session Manager]
    Router <--> Memory[AgentCore Memory]
    Listener -. OTEL .-> Observe[AgentCore Observability]
    Executor -. OTEL .-> Observe
    Ambiguity[Strands Swarm] -. semantic ambiguity only .-> Ledger
```

The deterministic target path is a Strands Graph:

```text
Listener -> Ledger Curator -> Risk Appraiser -> Quorum Router -> Executor
```

Strands Swarm is intentionally excluded from routine orchestration. It is reserved for bounded semantic ambiguity resolution. Strands hook interrupts implement the autonomy gate before consequential tool calls.

The executable graph now constructs all five required nodes:

```text
Listener -> Ledger Curator -> Risk Appraiser -> Quorum Router -> Executor
```

The Listener and Ledger Curator are typed model-backed `Agent` nodes. The Risk Appraiser and Quorum
Router are deterministic Strands-compatible nodes: model prose cannot change the risk score,
autonomy level, quorum size, timeout, or interrupt spend. The Executor is a real `Agent` whose tool
boundary is guarded by `BeforeToolCallEvent` and `event.interrupt()`. It re-reads the persisted policy
by organization and action ID before allowing a tool call. Missing, mismatched, rejected, expired, or
budget-deferred decisions fail closed.

The fixed risk rubric scores reversibility, impact radius, and money impact. High-risk actions always
require two people, even at maximum autonomy. Lower-risk actions require the minimum safe quorum; a
low-risk action may become silent only after earned autonomy. Each person's interrupt budget is two
requests in a rolling seven-day window. When the first candidate is exhausted, routing moves to the
next eligible person; when no minimum quorum remains, the action is deferred instead of exceeding the
budget. All pending decisions have a 24-hour timeout. Low-risk timeouts may execute and notify, while
medium- and high-risk timeouts expire without action. Three consecutive approvals raise autonomy by
one level; a rejection or undo lowers it by one.

`DatabaseLedger` and `DecisionPolicyStore` use SQLite for zero-setup local development and PostgreSQL
with psycopg 3 for the production path. Both use the same SQLAlchemy domain implementation and
Alembic schema. Decision IDs are idempotent, mutable policy rows are transactionally locked, all
queries are tenant-scoped, and interrupt evidence is append-only. Raw message text is not stored in
these tables. The current executor intentionally returns an `authorized_dry_run` receipt; real
calendar, email, and form side effects belong to the next milestone.

Runtime session identifiers, session-state persistence, and long-term memory are separate concerns:

- AgentCore Runtime identifies a runtime session.
- A Strands session manager persists graph and conversation state.
- AgentCore Memory stores durable organization facts and summaries.
- PostgreSQL stores authoritative business facts, idempotency records, commitment audit events,
  autonomy profiles, policy decisions, and interrupt-budget events.

These stores are complementary. PostgreSQL is not presented as AgentCore Memory, and a Runtime
session ID is not used as a persistence mechanism.

### Database configuration

Without configuration, Quorum uses `sqlite+pysqlite:///./var/quorum.sqlite3`. Production must set a
dedicated PostgreSQL URL using psycopg 3 and transport encryption:

```bash
export QUORUM_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/quorum?sslmode=require'
uv run quorum-db upgrade
uv run quorum-db current
uv run quorum-db check
```

Do not place credentials in `.env.example`, logs, screenshots, or committed files. The application
hides SQL parameters and applies UTC, statement-timeout, lock-timeout, and connection-health
settings to PostgreSQL connections.

See [API.md](API.md) and [Method.md](Method.md) for the versioned contracts.

## Evaluation as a product feature

The repository includes a 50-case, reviewable synthetic commitment-extraction set. It contains
39 expected commitment operations, 10 pure negative cases, 4 ambiguity cases, and coverage of all
six frozen task classes. Every case carries provenance and measures:

- exact-match case accuracy;
- miss rate over gold commitments;
- hallucination rate over predictions, where an output without valid source evidence is always a
  hallucination and is rejected before persistence.

The committed `empty-baseline-v1` run verifies the metric pipeline; it is deliberately not a model
result: 20.0% exact-match accuracy, 100.0% miss rate, and 0.0% hallucination rate. The apparently
non-zero accuracy comes only from ten pure negative cases. See the
[evaluation specification](docs/evaluation/commitment-ledger-eval-v1.md) and
[machine-readable report](reports/eval/empty_baseline_v1.json).

Impact evidence will compare the same week under two conditions and report message count, closed decisions, decision-latency P50, total interruptions, and undo rate. Until real consented data exists, the replay demo will use visibly labeled synthetic data and no impact claim will be presented as real-world evidence.

## Privacy and safety

- Raw chat exports are processed locally and are ignored by Git.
- PII is redacted before data enters model, storage, logs, or evaluation artifacts.
- The business database stores opaque identifiers and source references, never raw message text.
- Logs contain opaque identifiers, counters, and trace IDs, never raw message text.
- Every ledger row requires a source-message reference.
- Money, broad-impact, or hard-to-reverse actions require an interrupt or sufficient quorum.
- Real quotations require explicit publication approval.
- Synthetic and real datasets remain physically and semantically separated.

The local redaction tool uses only the Python standard library:

```bash
export QUORUM_REDACTION_KEY='use-a-long-local-secret'
python3 tools/redact_chat.py \
  --input ./data/raw/export.json \
  --output ./data/redacted/export.json \
  --report ./data/redacted/report.json
python3 -m unittest discover -s tests -v
```

Do not reuse a published example key for real data. The key must never be committed.

## Known limits

- The real-time channel target is Slack. Discord is not in the initial build.
- We do not claim real-time WeChat or WhatsApp ingestion. A consented export may be replayed offline after local redaction.
- The current project has no recruited pilot organization and therefore no real-week impact result yet.
- No Bedrock model score is published because this development environment has no configured AWS
  CLI, region, or credentials.
- The five-node Graph structure, deterministic nodes, and native hook interrupt/resume behavior are
  tested locally. A live Bedrock end-to-end Graph run is not claimed.
- PostgreSQL DDL is compiled and asserted in tests, while the repository's integration suite runs
  against SQLite. A live PostgreSQL network integration result will only be claimed after a
  dedicated test endpoint is available.
- The current executor is a non-mutating authorization receipt. Calendar, email, and form actions
  will be enabled only after their real SDK or MCP contracts and undo behavior are tested.

## Reuse and AI assistance disclosure

This repository was created during the hackathon submission period. As of the initial repository version, it incorporates no pre-existing application code. Standard libraries, open-source dependencies, official examples, and templates will be listed with their licenses as they are introduced. AI coding assistance is used for implementation, review, testing, and documentation; the entrant remains responsible for every submitted artifact.

## Competition delivery checklist

- [x] Public repository target and Apache-2.0 license
- [x] English README and explicit current limitations
- [x] Privacy-first local redaction tool and test plan
- [x] Typed Commitment Ledger with SQLite development and PostgreSQL production persistence
- [x] Alembic schema, message idempotency, tenant isolation, and append-only audit events
- [x] Five-node Strands Graph constructor with deterministic Risk Appraiser and Quorum Router
- [x] Deterministic source-evidence gate
- [x] Deterministic risk rubric, Autonomy Ladder, minimum-quorum routing, and 24-hour defaults
- [x] Rolling seven-day, two-interrupt-per-person budget with append-only evidence
- [x] Strands-native `BeforeToolCallEvent` / `event.interrupt()` autonomy gate
- [x] 50-case synthetic gold set and executable metric pipeline
- [ ] Bedrock model evaluation result
- [ ] AgentCore Runtime, Memory, Gateway, and OTEL traces
- [ ] Anonymous synthetic replay demo
- [x] Honest empty-baseline evaluation report
- [ ] Consented real-organization comparison and quotation
- [ ] Architecture diagram asset
- [ ] Public YouTube or Vimeo video no longer than five minutes
- [ ] Three public Builder Center posts with `Agents for Humans` in each title
- [ ] AWS Builder ID added to the Devpost submission

## License

Licensed under the [Apache License 2.0](LICENSE).
