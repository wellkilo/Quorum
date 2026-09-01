# Quorum

> A group coordination agent whose primary success metric is how rarely it interrupts people.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Hackathon](https://img.shields.io/badge/AWS-Agents%20for%20Humans-ff9900)](https://agentsforhumans.devpost.com/)
[![Track](https://img.shields.io/badge/Track-Good%20Neighbor%20Agents-2f855a)](https://agentsforhumans.devpost.com/)
[![AgentCore Runtime evidence](https://github.com/wellkilo/Quorum/actions/workflows/deploy-agentcore.yml/badge.svg)](https://github.com/wellkilo/Quorum/actions/runs/33393010402)

Quorum coordinates the routine decisions that exhaust small communities. It listens where the group already talks, turns commitments into an evidence-linked ledger, acts autonomously only within earned boundaries, and asks the minimum number of people required for a safe decision.

Its product promise is deliberately unusual: **the best coordination agent is the one you barely notice.**

## Status

Quorum is an active entry in the 2026 AWS Agents for Humans Hackathon. The current executable slice includes a typed commitment ledger, the complete five-node Strands Graph structure, deterministic risk and routing, a Strands-native hook interrupt autonomy gate, reversible Google Calendar, Gmail Draft, and Google Forms tools, all three Slack interaction adapters, transactional SQLite/PostgreSQL persistence, AgentCore Runtime/Memory/Gateway adapters, PII-safe OpenTelemetry spans, signed single-use undo, an anonymous synthetic replay UI, Alembic migrations, and a 50-case synthetic evaluation suite. A short-lived AgentCore Runtime deployment has been verified through GitHub OIDC; live Slack, Google Workspace, AgentCore Memory, and AgentCore Gateway calls are not yet claimed.

No real organization data, user quote, impact result, continuously hosted Runtime, or Bedrock model score is claimed at this stage. Every current evaluation case is labeled `synthetic` in its metadata.

## Public synthetic replay

**Live demo:** https://wellkilo.github.io/Quorum/

The GitHub Pages demo is an anonymous, versioned static replay of the same synthetic evidence
contract used by the local Runtime. It demonstrates the interaction and measurement design without
claiming a live AgentCore backend or real-world impact. The page labels both limitations directly.

The recording-ready presentation package includes the [4:50 narration](docs/video/script.md),
[shot-by-shot storyboard](docs/video/storyboard.md), [English WebVTT captions](docs/video/quorum-demo.en.vtt),
and [recording checklist](docs/video/recording-checklist.md). It intentionally preserves an honest
no-quote segment until a participant approves real wording.

Three publication-ready [Builder Center drafts](docs/blog/README.md) document the product thesis,
the native-interrupt architecture, and the evidence-first evaluation approach. They remain drafts
until their public URLs are verified.

## Run the verified local slice

Python 3.11–3.13 and [`uv`](https://docs.astral.sh/uv/) are required.

```bash
uv sync --extra dev --extra postgres --extra runtime
uv run quorum-db upgrade
uv run quorum-db check
uv run quorum-validate-gold
uv run python -m unittest discover -s tests -v
uv run ruff format --check src migrations tests scripts
uv run ruff check src migrations tests scripts
uv run mypy src/quorum
uv build
```

The Graph constructor uses the installed `strands-agents==1.53.0` package. It can be built without
making a model request. Online extraction requires an explicitly selected AWS region and valid
credentials through the standard boto3 credential chain; Quorum does not guess a region.

Google Workspace execution uses Application Default Credentials. Slack delivery uses a bot token,
and undo URLs require a secret of at least 32 bytes. Keep all three outside source control:

```bash
export QUORUM_UNDO_SIGNING_SECRET='<at-least-32-random-bytes>'
export QUORUM_PUBLIC_BASE_URL='https://your-public-origin.example'
export QUORUM_SLACK_BOT_TOKEN='<injected-secret>'
export QUORUM_SLACK_SIGNING_SECRET='<injected-secret>'
export QUORUM_SLACK_PSEUDONYM_KEY='<separate-injected-secret>'
```

Preview the exact three-message synthetic Slack smoke path without credentials or network calls:

```bash
uv run quorum-slack-smoke
```

To post it to a dedicated test workspace, inject a bot token with `chat:write` and `im:write`, add
the bot to the test channel, set `QUORUM_SLACK_DEMO_CHANNEL_ID` and
`QUORUM_SLACK_DEMO_PARTICIPANT_ID`, then explicitly confirm the three live posts:

```bash
uv run quorum-slack-smoke --confirm-live-posts
```

The receipt, direct question, and weekly summary are visibly labeled synthetic. The command makes
zero Bedrock calls and zero Google or AgentCore Gateway tool calls. It prints message timestamps but
never the token or message content.

The anonymous replay requires no credentials and is deliberately synthetic. Start the exact
AgentCore-compatible ASGI application locally, then open `http://127.0.0.1:8080`:

```bash
uv run quorum-runtime
```

Press **Replay the synthetic week** to see the same fixed demonstration dataset produce the visible
before/after metrics and receipt trail in under ten seconds. This validates the public surface, not
a real-world impact result.

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

![Quorum system architecture](assets/quorum-architecture.svg)

Download the [1600x900 PNG](assets/quorum-architecture.png) for slides or video editing, or use the
[SVG source](assets/quorum-architecture.svg) for lossless publication.

Solid components are implemented and tested locally. The AgentCore Runtime node records a verified
short-lived deployment; dashed AgentCore Memory and Gateway boxes remain undeployed targets. The
public GitHub Pages site is a separate static synthetic evidence surface, not the Runtime shown in
the hosted path.

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

`DatabaseLedger`, `DecisionPolicyStore`, and `ExecutionStore` use SQLite for zero-setup local
development and PostgreSQL with psycopg 3 for the production path. They share one SQLAlchemy domain
implementation and Alembic schema. Decision and execution IDs are idempotent, mutable rows are
transactionally locked, all queries are tenant-scoped, and interrupt and execution evidence are
append-only. Raw messages and action arguments are not stored; only canonical SHA-256 fingerprints,
opaque provider resource IDs, safe status codes, and timestamps are retained.

The Executor exposes three real Strands tools: a tentative Calendar event, a Gmail draft that is
never sent, and a Google Form response request. The native autonomy hook verifies the persisted tool
name and canonical argument fingerprint before any provider call. Successful execution produces one
Slack line with Open and Undo buttons. Undo tokens are HMAC-SHA256 signed, expire after 24 hours, are
stored only as digests, and are atomically consumed before the provider resource is deleted. An undo
also lowers earned autonomy. Transport errors and ambiguous provider responses become `uncertain`
and are never retried automatically, preventing duplicate external side effects.

Runtime session identifiers, session-state persistence, and long-term memory are separate concerns:

- AgentCore Runtime identifies a runtime session.
- A Strands session manager persists graph and conversation state.
- AgentCore Memory stores durable organization facts and summaries.
- PostgreSQL stores authoritative business facts, idempotency records, commitment audit events,
  autonomy profiles, policy decisions, and interrupt-budget events.

These stores are complementary. PostgreSQL is not presented as AgentCore Memory, and a Runtime
session ID is not used as a persistence mechanism.

### AgentCore deployment evidence and inputs

The repository contains executable adapters for AgentCore Runtime, Memory, and an IAM-authenticated
Gateway Lambda target. The Runtime-only path was exercised in `ap-northeast-1` on August 31, 2026:
GitHub OIDC assumed a scoped deployer role, built a Python 3.13 arm64 CodeZip, reached `READY`,
observed the disabled model path return HTTP `503`, and deleted the Runtime, managed workload
identity, archive, and temporary bucket. See the
[deployment evidence record](docs/evidence/agentcore-runtime-2026-08-31.md) and the
[successful workflow](https://github.com/wellkilo/Quorum/actions/runs/33393010402).

The production model path is disabled by default. Deployment, health checks, and the synthetic demo
cannot invoke Bedrock until an operator deliberately opens the cost gate for a bounded run:

```bash
export QUORUM_BEDROCK_ENABLED='true'
export QUORUM_BEDROCK_MAX_TOKENS='384'
```

Keep the gate closed during infrastructure provisioning and static demo verification. The token cap
limits output per call; it is not an AWS account-level spending cap.

```bash
uv run quorum-provision-memory --region '<aws-region>'
uv run quorum-provision-gateway \
  --region '<aws-region>' \
  --role-arn '<gateway-service-role-arn>' \
  --lambda-arn '<execution-lambda-arn>'

export QUORUM_AGENTCORE_MEMORY_ID='<memory-id-from-the-first-command>'
export QUORUM_AGENTCORE_GATEWAY_URL='<https-gateway-url>/mcp'
```

The deployment CLI interface was checked with `@aws/agentcore@0.28.1`; it requires Node.js 20 or
later. After installing it, configure `src/quorum/runtime.py` as the Python entrypoint and deploy from
an AWS-authenticated environment. The older Python Starter Toolkit CLI now prints a deprecation
warning, so it is not the documented production path. Exact cloud commands will be checked in only
after they are exercised against the target AWS account.

The checked-in `Deploy AgentCore Runtime` GitHub Actions workflow is manual-only and authenticates
through GitHub OIDC. It deploys a Python 3.13 arm64 CodeZip without ECR or CodeBuild. The execution
role has an explicit deny for `bedrock:InvokeModel*`, while the runtime environment keeps
`QUORUM_BEDROCK_ENABLED=false`. Runtime verification intentionally invokes the disabled path once,
requires same-session CloudWatch evidence for HTTP `503`, and stops that session immediately. The
workflow then deletes the Runtime and temporary private S3 artifact bucket whether verification
succeeds or fails.

The separate manual `Verify AgentCore Memory and Gateway` workflow is the next deployment gate. It
builds the real Gateway Lambda package, creates one short-lived empty Memory and one IAM-authenticated
Gateway target, verifies Memory strategy metadata and MCP `tools/list`, and cleans every resource.
The verification contract creates zero Memory events, makes zero Gateway tool calls, keeps
`QUORUM_BEDROCK_ENABLED=false`, and keeps `QUORUM_EXECUTION_ENABLED=false`. Its implementation is
checked in and locally tested; no successful cloud run is claimed until a public Actions run exists.

This deployment is evidence for AgentCore Runtime hosting only. The public anonymous experience
remains GitHub Pages; AgentCore's invoke API is IAM-authenticated. PostgreSQL, AgentCore Memory, and
Gateway are separate production integrations and are not implied by a Runtime-only deployment.

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
- Action payloads such as email recipients, bodies, subjects, event titles, and form questions are
  fingerprinted for authorization but are not persisted in the business database.
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
- No Bedrock model score is published because model calls remain deliberately disabled in both the
  Runtime environment and IAM policy.
- The five-node Graph structure, deterministic nodes, and native hook interrupt/resume behavior are
  tested locally. A live Bedrock end-to-end Graph run is not claimed.
- PostgreSQL DDL is compiled and asserted in tests, while the repository's integration suite runs
  against SQLite. A live PostgreSQL network integration result will only be claimed after a
  dedicated test endpoint is available.
- Google Calendar, Gmail Draft, Google Forms, and Slack adapters use their real SDK method contracts,
  but tests replace only the network boundary. The synthetic three-surface Slack smoke path is
  executable and fail-closed; no live external API result is claimed yet.
- The undo transport is implemented and locally tested: `GET` renders a confirmation page and only
  `POST` consumes the token. Its public HTTPS deployment is not yet claimed.
- The three tools have typed AgentCore Gateway schemas, an IAM MCP client, and a Lambda dispatch
  adapter. The real arm64 Lambda package and short-lived verification workflow are implemented, but
  no successful Gateway cloud run is claimed yet.
- AgentCore Memory provisioning and Strands session-manager wiring are implemented and tested against
  the installed SDK contract. The zero-event verification lifecycle is implemented, but no successful
  Memory cloud run is claimed yet.
- OpenTelemetry correlation spans and Strands sensitive-attribute redaction are configured in code.
  Runtime logs verify the same-session HTTP `503`; no managed OTEL trace screenshot is claimed yet.
- The anonymous GitHub Pages replay is public and visibly labels every result synthetic. It uses a
  versioned static evidence fixture and is not presented as the IAM-authenticated AgentCore Runtime.

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
- [x] Tool-name and canonical-argument binding between approval and execution
- [x] Reversible Google Calendar, Gmail Draft, and Google Forms SDK adapters
- [x] Idempotent execution receipts, append-only audit, and signed single-use 24-hour undo
- [x] Slack one-line group receipt, one-question direct message, and one-screen weekly summary
- [x] 50-case synthetic gold set and executable metric pipeline
- [ ] Bedrock model evaluation result
- [x] AgentCore Runtime, Memory session manager, Gateway MCP, and safe OTEL integration code
- [x] Public anonymous synthetic replay demo
- [x] Short-lived AgentCore Runtime deployment, HTTP 503 cost-gate evidence, and automatic cleanup
- [ ] Deployed AgentCore Memory, Gateway, and managed OTEL trace evidence
- [x] Public anonymous replay URL
- [x] Honest empty-baseline evaluation report
- [ ] Consented real-organization comparison and quotation
- [x] Architecture diagram asset
- [ ] Public YouTube or Vimeo video no longer than five minutes
- [ ] Three public Builder Center posts with `Agents for Humans` in each title
- [ ] AWS Builder ID added to the Devpost submission

## License

Licensed under the [Apache License 2.0](LICENSE).
