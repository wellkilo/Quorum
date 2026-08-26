#!/usr/bin/env python3
"""Write an honest no-extraction baseline for metric-pipeline validation."""

from __future__ import annotations

from pathlib import Path

from quorum.evaluation import PredictionCase
from quorum.gold import DEFAULT_GOLD_PATH, load_gold_cases
from quorum.models import ExtractionEnvelope

OUTPUT = Path("data/eval/predictions/empty_baseline_v1.jsonl")


def main() -> int:
    predictions = [
        PredictionCase(case_id=case.case_id, prediction=ExtractionEnvelope()).model_dump_json()
        for case in load_gold_cases(DEFAULT_GOLD_PATH)
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(predictions) + "\n", encoding="utf-8")
    print(f"wrote {len(predictions)} empty-baseline predictions to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
