#!/usr/bin/env bash
set -euo pipefail

uv sync --extra dev --extra postgres --extra runtime --locked
uv run ruff format --check src migrations tests scripts
uv run ruff check src migrations tests scripts
uv run mypy src/quorum
uv run python -m unittest discover -s tests -v
uv run quorum-validate-gold
uv run python scripts/build_empty_baseline.py
uv run quorum-eval \
  --predictions data/eval/predictions/empty_baseline_v1.jsonl \
  --predictor empty-baseline-v1 \
  --output reports/eval/empty_baseline_v1.json >/dev/null
git diff --check
