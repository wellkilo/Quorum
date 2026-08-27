# Agents for Humans: Testing an Agent That Must Not Invent Commitments

An assistant that misses a reminder can be annoying. A coordination agent that invents a promise can
damage trust between people. Quorum therefore treats evaluation as a product feature, not a final
leaderboard exercise.

I work in quality assurance, so I wanted the repository to answer a harder question than “does the
demo look plausible?” The question is: what observable evidence would prove that the agent extracted
the right commitment, preserved its source, respected its interruption budget, and refused unsafe
execution?

## The non-negotiable ledger invariant

Every Commitment Ledger mutation must carry two linked pieces of evidence: a source-message
reference and a verbatim span from that message. If the reference does not identify the current
event, or the quoted span is not actually present, Quorum rejects the candidate before persistence.

That rule changes the meaning of hallucination in the evaluation. A prediction can sound correct and
still count as hallucinated if it cannot prove where it came from. This is useful for coordination
because a participant can inspect the original words instead of trusting a polished model summary.

The same rule applies to updates and cancellations. The target commitment must be named in the
message, and the audit log preserves the evidence chain across mutations. Duplicate delivery is
idempotent, while reusing the same message identity with changed content is rejected.

## A 50-case synthetic gold set

The versioned dataset contains exactly 50 synthetic cases:

- 39 expected commitment operations across 36 cases;
- 10 pure negatives that must create no ledger entry;
- 4 ambiguity cases that must be surfaced without guessing;
- all six frozen task classes;
- create, update, cancel, conditional, multi-commitment, and evidence-grounding cases.

Every record says `data_classification: synthetic`. The names are fictional, and no message was
exported from a real organization. This matters because a benchmark should not quietly become a
privacy liability.

The evaluator reports three core measures:

- **Exact-match case accuracy**: the complete grounded output matches the gold case.
- **Miss rate**: expected commitments not recovered, divided by all gold commitments.
- **Hallucination rate**: unmatched or evidence-rejected commitments, divided by all predictions.

The report also exposes the number of evidence rejections. Matching includes operation, task class,
normalized summary, owner, due time, and mutation target. It deliberately checks source grounding
before scoring semantic fields.

## Why publish a deliberately bad baseline?

The committed `empty-baseline-v1` returns no commitments. It scores 20 percent exact-match accuracy,
100 percent miss rate, and zero percent hallucination rate. That first number looks less terrible than
the predictor really is because it gets the ten pure-negative cases right by doing nothing.

This is precisely why the baseline is useful. It proves that accuracy alone is misleading and that a
zero hallucination rate is meaningless without miss rate. It also verifies dataset loading, matching,
aggregation, and machine-readable report generation before an online model result is introduced.
No Bedrock score is published yet because the current AWS account cannot run the intended model
evaluation.

## Negative tests for evidence claims

The same fail-closed principle protects the public demo. GitHub Pages serves a versioned static
snapshot because it cannot run the Python Runtime. A builder copies only five expected files, and a
validator checks project-relative asset paths, the synthetic provenance label, the real-world-impact
disclaimer, the two-interrupt limit, and the fixed replay counters.

The test suite then deliberately changes `data_classification` from `synthetic` to `real`. The
validator must reject the artifact with `public replay must be classified as synthetic`. This is a
small negative test, but it protects a high-consequence boundary: the published page cannot silently
turn a synthetic scenario into an impact claim.

The static fixture is also compared field by field with the Runtime replay response, excluding only
the generated replay ID. That catches a subtler failure mode where the polished public page and the
executable application drift into different stories.

## Test the policy, not only the prompt

Quorum's current local suite covers far more than extraction examples. It tests:

- tenant isolation and append-only ledger, interrupt, and execution evidence;
- the three-axis deterministic risk rubric;
- autonomy promotion after three approvals and demotion after rejection or undo;
- rolling seven-day interruption spend and rerouting around an exhausted participant;
- one- and two-person native Strands interrupts and resume behavior;
- tool-name and canonical-argument binding before execution;
- idempotent provider calls, single-use undo, and uncertain outcomes that must not retry;
- Slack signature replay protection and PII redaction before Graph input;
- the architecture image, 4:50 caption track, and public evidence language.

At the time of writing, 134 local tests pass in the locked project environment. This is a local test
result, not a cloud deployment claim. PostgreSQL DDL is compiled and checked, while integration tests
currently use SQLite; real PostgreSQL network evidence will be published only after a dedicated
endpoint exists. Provider SDK calls are tested at their network boundaries, not presented as live
Google or Slack traffic.

## Reproduce the evidence

```bash
uv sync --extra dev --extra postgres --extra runtime
uv run quorum-validate-gold
uv run python scripts/build_empty_baseline.py
uv run quorum-eval \
  --predictions data/eval/predictions/empty_baseline_v1.jsonl \
  --predictor empty-baseline-v1 \
  --output reports/eval/empty_baseline_v1.json
uv run python -m unittest discover -s tests -v
```

The public synthetic replay is at <https://wellkilo.github.io/Quorum/> and the source is at
<https://github.com/wellkilo/Quorum>. A consented real-world pilot and an online Bedrock evaluation
remain future evidence gates, not placeholders to be filled with assumptions.

For an agent whose goal is to interrupt less, correctness includes knowing when not to write, not to
ask, not to retry, and not to claim.
