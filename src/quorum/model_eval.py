"""Run the real Strands/Bedrock extractor over the versioned gold dataset."""

from __future__ import annotations

import argparse
from pathlib import Path

from quorum.evaluation import PredictionCase
from quorum.gold import DEFAULT_GOLD_PATH, load_gold_cases
from quorum.orchestration import (
    BedrockSettings,
    build_bedrock_model,
    build_ledger_graph,
    event_to_graph_task,
    extraction_from_result,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the real Strands Graph against the commitment gold dataset."
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    settings = BedrockSettings.from_environment()
    model = build_bedrock_model(settings)
    predictions: list[str] = []
    for case in load_gold_cases(args.gold):
        # Fresh agents prevent one gold case from leaking context into the next.
        graph = build_ledger_graph(model=model)
        result = graph(event_to_graph_task(case.event))
        prediction = PredictionCase(case_id=case.case_id, prediction=extraction_from_result(result))
        predictions.append(prediction.model_dump_json())

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(predictions) + "\n", encoding="utf-8")
    print(f"wrote {len(predictions)} predictions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
