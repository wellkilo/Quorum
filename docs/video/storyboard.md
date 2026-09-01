# Quorum — 4:50 Storyboard

Record at 1920x1080. Keep the cursor slow and deliberate, and preserve the visible “synthetic data
only” label whenever replay numbers are on screen. Do not show personal browser data, AWS account
details, Slack tokens, email addresses, or local environment variables.

| Time | Picture | Operator action | Evidence on screen |
| --- | --- | --- | --- |
| 0:00–0:08 | Tight crop of the public replay baseline | Load `https://wellkilo.github.io/Quorum/` in a signed-out window | `Public static replay · synthetic data only` |
| 0:08–0:25 | Baseline metrics | Pan from `214` to `3` and `3.1d`; do not click yet | Same-week baseline, visibly synthetic |
| 0:25–0:50 | Hero claim and budget | Hold on “when not to appear,” then frame `≤ 2` | Product thesis and rolling-seven-day budget |
| 0:50–1:05 | Replay start | Click **Replay the synthetic week** once | Button state changes to replaying |
| 1:05–1:25 | Replay trace | Follow the first two timeline entries | Source-linked Commitment Ledger |
| 1:25–1:55 | Receipts | Hold on all three receipts; point to Undo and “draft — not sent” | Reversible tool boundaries |
| 1:55–2:30 | Quorum result | Frame six appearances and the two-person budget markers | Minimum quorum and Interrupt Budget |
| 2:30–3:20 | Architecture PNG, then two successful GitHub Actions runs | Reveal Graph left to right; show one synthetic managed span + HTTP 503 + cleanup, then Memory ACTIVE + Gateway READY + zero events/calls + cleanup | Graph, native hook, managed observability, and three verified short-lived AgentCore lifecycles |
| 3:20–3:50 | Side-by-side replay | Return to the completed public page | 214 → 6 appearances; 3 → 6 decisions; 74.4h → 7h |
| 3:50–4:15 | Evaluation evidence | Show README evaluation table and `data/eval/commitment_gold_v1.jsonl` file count | 50 synthetic gold cases and evidence-grounding rule |
| 4:15–4:35 | Evidence-boundary title card | Show “No consented pilot quote yet — no quote fabricated” | Honest limitation; replace only after consent |
| 4:35–4:46 | Repository and public demo | Show repository root, Apache-2.0 `LICENSE`, then Pages URL | Public source and anonymous demo |
| 4:46–4:50 | Submission identity | Show only the actual AWS Builder ID field after it has been entered | Actual entrant identity; never a placeholder |

## Architecture reveal order

1. Trace the solid main path from Slack/Runtime input through all five Graph nodes.
2. Point to the Swarm dotted branch and say “semantic ambiguity only.”
3. Stop on the green diamond for the native hook interrupt and its fail-closed behavior.
4. Move to Gateway and the three reversible tools.
5. Finish on the separate state row: Runtime, Memory, and Gateway were verified only as short-lived lifecycles and then cleaned; OpenTelemetry shows one synthetic zero-call managed span, not a production trace.

## Recording substitutions

- If a consented pilot exists, replace only 4:15–4:35 with the approved quote card.
- Show the two successful workflow runs rather than a live AWS console. Together they contain the
  real Runtime READY and HTTP 503 evidence, one validated synthetic managed span, Memory ACTIVE,
  Gateway READY and tool list, zero downstream calls, and cleanup without exposing account details.
- Do not imply that any AgentCore resource is continuously deployed, and do not simulate a
  participant, Gateway execution, or production trace.
- Do not simulate a cloud console or deployed resource. For the managed span, show only the real
  GitHub Actions output and the public evidence record.
