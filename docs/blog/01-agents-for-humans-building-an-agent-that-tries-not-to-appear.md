# Agents for Humans: Building an Agent That Tries Not to Appear

Most coordination software measures engagement. Quorum starts from the opposite question: what if success means the software appears less often?

Small community groups rarely fail because nobody cares. They fail in the space between a message and a commitment. Someone says they can bring the keys. Another person suggests Friday. A third assumes the decision was final. A week of conversation later, the group has produced plenty of activity but little shared certainty.

I am building Quorum for the AWS Agents for Humans Hackathon in the Good Neighbor Agents track. It is a group coordination agent with one primary metric: interruptions per person per week. The default budget is two. Once the budget is spent, the agent must batch, reroute, wait for its published timeout, or choose the safe default. It cannot solve coordination by producing more notifications.

## Four mechanisms, one product claim

Quorum combines four mechanisms that have to work together.

The Commitment Ledger extracts promises and decisions, but every row must point back to a source message. If the system cannot identify evidence, it does not create the record. This turns provenance from a footnote into an invariant.

The Autonomy Ladder lets low-risk actions earn independence. Repeated approval can move an action from ask-first to notify-and-undo. A rejection, reversal, broader impact, or money resets that confidence.

Minimum-Quorum Routing asks the smallest sufficient set of accountable people. It is not consensus theater. A room-booking correction may need one owner; spending community funds may need two; a public commitment may need a broader decision. Every request includes a timeout and the action that silence will trigger.

The Interrupt Budget forces the rest of the design to stay honest. The agent cannot hide bad product decisions behind unlimited private messages.

## Why Strands Graph and interrupts matter

The main orchestration path will be a deterministic Strands Graph:

```text
Listener -> Ledger Curator -> Risk Appraiser -> Quorum Router -> Executor
```

The model helps interpret messy language, but it does not get to bypass policy. Before a consequential tool call, a Strands hook evaluates reversibility, impact radius, spending, earned autonomy, quorum, timeout behavior, and interrupt spend. If human judgment is required, the hook raises a native interrupt and execution pauses until the response is bound to that interrupt.

Strands Swarm is reserved for the narrow case where meaning is genuinely ambiguous. Routine orchestration stays inspectable. Amazon Bedrock AgentCore will provide the runtime, durable organizational memory, MCP tools through Gateway, and OpenTelemetry-compatible observability. Runtime sessions, orchestration state, and long-term memory will remain explicit separate layers.

## Evidence before impact claims

There is no pilot organization yet, so there is no real-world result to report. Early demos will use data that is visibly labeled synthetic. The public repository includes a local redaction tool and consent template before it includes a real chat export.

The evaluation plan reflects my quality-engineering background. A 50-case human-labeled set will measure commitment-extraction precision, miss rate, and hallucination rate. A prediction without a valid source-message reference will count as hallucinated even if it sounds plausible.

For impact, the same week will eventually be replayed under two conditions. The comparison will report messages, closed decisions, decision-latency P50, total interruptions, and undo rate. A participant quotation will appear only if a real participant approves its exact final wording.

## The first build decision

The first artifact was not an agent prompt. It was a boundary: no raw personal data in Git, no unverified product claim in the README, and no action without a documented authority path.

That may look slower than starting with a chatbot. It is the shortest path to the product I want to demonstrate: an agent that can earn enough trust to stay quiet.

The next build milestone is a vertical slice in Slack: one message becomes one evidence-linked commitment, passes through the risk and quorum policies, and produces either one private question or one reversible group receipt. The counter that matters will already be visible: how many times did Quorum decide not to interrupt?
