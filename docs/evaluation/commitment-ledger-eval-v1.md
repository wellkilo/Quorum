# Commitment Ledger Evaluation v1

Status: executable synthetic benchmark; no real-organization or online-model result is claimed.

## Why this exists

Quorum's first irreversible quality rule is simple: a commitment cannot enter the ledger unless
it points to the source message and quotes evidence that appears verbatim in that message. The
evaluation suite measures whether extraction remains useful after enforcing that rule.

## Dataset

`data/eval/commitment_gold_v1.jsonl` contains exactly 50 reviewable synthetic cases:

- 39 expected commitment operations across 36 cases;
- 10 pure negative cases that must not create a ledger entry;
- 4 ambiguity cases that must be surfaced without guessing;
- all six frozen task classes;
- create, update, cancel, multi-commitment, conditional, and evidence-grounding cases.

Every record and embedded event declares `data_classification: synthetic`. Names are not real
people, and the messages were not exported from a real organization.

## Metrics

- **Exact-match accuracy**: cases whose grounded commitments and ambiguity fields exactly match
  the gold case, divided by 50.
- **Miss rate**: unmatched gold commitments divided by all gold commitments.
- **Hallucination rate**: unmatched or evidence-rejected predicted commitments divided by all
  predicted commitments. If a predictor emits no commitments, this value is reported as zero and
  must be read together with miss rate.
- **Evidence rejections**: predictions blocked because the source reference differs from the event
  or the quoted evidence is not a verbatim substring of the event text.

Matching deliberately excludes confidence and evidence-quote wording after evidence validation. It
includes operation, task class, normalized summary, owner, due time, and mutation target.

## Reproduce

```bash
uv sync --extra dev
uv run python scripts/build_gold_dataset.py
uv run quorum-validate-gold
uv run python scripts/build_empty_baseline.py
uv run quorum-eval \
  --predictions data/eval/predictions/empty_baseline_v1.jsonl \
  --predictor empty-baseline-v1 \
  --output reports/eval/empty_baseline_v1.json
```

The committed empty baseline is a metric-pipeline check, not a language-model result:

| Predictor | Exact-match accuracy | Miss rate | Hallucination rate |
| --- | ---: | ---: | ---: |
| `empty-baseline-v1` | 20.0% | 100.0% | 0.0% |

The 20% exact-match value comes from the ten pure negative cases. The four ambiguity cases do not
match an empty prediction.

## Run the real Bedrock evaluation

Quorum never guesses an AWS region. Configure valid AWS credentials through the standard boto3
credential chain and then run:

```bash
export QUORUM_AWS_REGION='<enabled-region>'
export QUORUM_BEDROCK_MODEL_ID='<enabled-model-id>'  # optional override
uv run quorum-run-model-eval --output data/eval/predictions/bedrock_v1.jsonl
uv run quorum-eval \
  --predictions data/eval/predictions/bedrock_v1.jsonl \
  --predictor bedrock-v1 \
  --output reports/eval/bedrock_v1.json
```

No Bedrock score is published yet because the current development machine has no AWS CLI, AWS
configuration directory, region environment variable, or credential environment variable.
