# Agents for Humans: Building an Agent That Tries Not to Appear

Most coordination software measures engagement. Quorum starts from the opposite question: what if
success means the software appears less often?

Small community groups rarely fail because nobody cares. They fail in the space between a message
and a commitment. Someone says they can bring the keys. Another person suggests Friday. A third
assumes the decision was final. A week of conversation later, the group has produced plenty of
activity but little shared certainty.

I am building Quorum for the AWS Agents for Humans Hackathon in the Good Neighbor Agents track. It
is a group coordination agent with one primary metric: interruptions per person per rolling week.
The default budget is two. Once the budget is spent, the agent must batch, reroute, wait for its
published timeout, or choose the safe default. It cannot solve coordination by producing more
notifications.

## Four mechanisms, one product claim

Quorum combines four mechanisms that have to work together.

The **Commitment Ledger** extracts promises and decisions, but every row must point back to a source
message. If the system cannot identify evidence, it does not create the record. This turns
provenance from a footnote into an invariant.

The **Autonomy Ladder** lets low-risk actions earn independence. Three consecutive approvals can
move an action up one level. A rejection or undo moves it down. Money, broad impact, and difficult
reversibility remain higher risk regardless of model confidence.

**Minimum-Quorum Routing** asks the smallest sufficient set of accountable people. It is not
consensus theater. A reversible correction may need one owner; spending community funds requires
two. Every request includes a 24-hour timeout and the action that silence will trigger.

The **Interrupt Budget** forces the rest of the design to stay honest. Each person gets at most two
decision requests in a rolling seven-day window. When the budget is unavailable, routing moves to
the next eligible person or defers the action. The agent cannot hide a weak policy behind unlimited
private messages.

## Why Strands Graph and interrupts matter

The main orchestration path is a five-node Strands Graph:

```text
Listener -> Ledger Curator -> Risk Appraiser -> Quorum Router -> Executor
```

The Listener and Ledger Curator use typed model output to interpret language. The Risk Appraiser and
Quorum Router are deterministic nodes. Model prose cannot change the risk score, autonomy level,
quorum size, timeout, or interruption spend. Strands Swarm is reserved for the narrow case where
meaning is genuinely ambiguous; it is not the routine orchestrator.

Before a consequential tool call, a Strands `BeforeToolCallEvent` hook loads the persisted policy,
verifies the tool name and canonical argument fingerprint, and calls the SDK's native interrupt
mechanism when approval is still required. Missing, expired, rejected, or mismatched decisions fail
closed. This matters because autonomy is not a setting around the agent. It is enforced at the tool
boundary.

## Build the evidence boundary before the claim

There is no pilot organization yet, so there is no real-world result or participant quotation to
report. The public demo therefore uses a versioned dataset that is labeled `synthetic` in both the
page and the JSON response. The current scenario shows 214 messages, three closed decisions, and a
74.4-hour median decision time before Quorum; the replay shows six total appearances, six closed
decisions, and a seven-hour median. Those values demonstrate the interaction and measurement
contract. They are not measured community impact.

The public replay is available at <https://wellkilo.github.io/Quorum/>. It is intentionally static,
anonymous, and visibly synthetic. Separately, a GitHub OIDC workflow deployed the reviewed CodeZip
to AgentCore Runtime, reached READY, verified that the disabled model path returned HTTP 503, and
deleted the short-lived resources. The same HTML and JavaScript call the real replay API when hosted
by the AgentCore-compatible application. A build-time validator rejects the public artifact if its
provenance label, interrupt budget, baseline, or disclaimer changes unexpectedly.

## Quality engineering is part of the product

The repository includes a 50-case synthetic gold set for commitment extraction. It covers creates,
updates, cancellations, multiple commitments, ambiguity, and pure negatives across six frozen task
classes. The evaluator reports exact-match accuracy, miss rate, and hallucination rate. A prediction
without a valid source reference and verbatim evidence span is rejected and counted as
hallucinated.

The committed empty baseline produces 20 percent exact-match accuracy, 100 percent miss rate, and
zero percent hallucination rate. That is not a useful model result: the 20 percent comes entirely
from ten negative cases where returning nothing happens to be correct. Publishing that intentionally
bad baseline makes the metric semantics inspectable before any Bedrock score exists.

The repository also tests tenant isolation, append-only audit evidence, rolling interruption spend,
multi-person native interrupts, single-use undo, provider uncertainty, static-demo provenance, and
the public architecture and video contracts.

## What is available now

- Public source: <https://github.com/wellkilo/Quorum>
- Anonymous synthetic replay: <https://wellkilo.github.io/Quorum/>
- Apache-2.0 license, reproducible local commands, and explicit known limits
- Strands Graph, native hook interrupt, AgentCore Runtime/Memory/Gateway adapters, PostgreSQL
  persistence, reversible tools, and PII-safe OpenTelemetry code
- Verified short-lived AgentCore Runtime hosting with zero model calls and automatic cleanup
- No claim yet of AgentCore Memory or Gateway deployment, real Slack or Google Workspace calls, a
  Bedrock model score, or real-world impact

The next evidence milestone is not another feature. It is a consented pilot plus Memory and Gateway
deployment evidence. Until those exist, Quorum will keep the boundary visible. An agent designed to
earn trust should be honest about what it has not earned yet.
