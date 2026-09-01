#!/usr/bin/env bash
set -euo pipefail

uv sync --extra dev --extra postgres --extra runtime --locked
uv run ruff format --check src migrations tests scripts
uv run ruff check src migrations tests scripts
uv run mypy src/quorum
uv run quorum-slack-socket validate >/dev/null
uv run python -m unittest discover -s tests -v
uv run quorum-validate-gold
uv run python scripts/build_empty_baseline.py
uv run quorum-eval \
  --predictions data/eval/predictions/empty_baseline_v1.jsonl \
  --predictor empty-baseline-v1 \
  --output reports/eval/empty_baseline_v1.json >/dev/null
uv build >/dev/null
uv run python - <<'PY'
from pathlib import Path
from zipfile import ZipFile

wheels = sorted(Path("dist").glob("quorum_agent-*.whl"))
if not wheels:
    raise SystemExit("built wheel was not found")
with ZipFile(wheels[-1]) as wheel:
    if "quorum/slack-app-manifest.json" not in wheel.namelist():
        raise SystemExit("Slack app manifest is missing from the built wheel")
PY
git diff --check
