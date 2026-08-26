"""Transparent commitment-extraction metrics for Quorum's gold dataset."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from quorum.gold import DEFAULT_GOLD_PATH, GoldCase, load_gold_cases
from quorum.ledger import evidence_rejection_code
from quorum.models import CommitmentCandidate, ExtractionEnvelope, StrictModel


class PredictionCase(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    case_id: Annotated[str, Field(pattern=r"^gold_[0-9]{3}$")]
    prediction: ExtractionEnvelope


@dataclass(frozen=True, slots=True)
class CandidateKey:
    operation: str
    task_class: str
    summary: str
    owner_id: str | None
    due_at: str | None
    target_commitment_id: str | None


class CaseEvaluation(StrictModel):
    case_id: str
    exact_match: bool
    gold_commitments: int
    predicted_commitments: int
    matched_commitments: int
    missed_commitments: int
    hallucinated_commitments: int
    rejected_for_evidence: int


class EvaluationReport(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    dataset: str
    data_classification: Literal["synthetic"] = "synthetic"
    predictor: str
    case_count: int
    gold_commitment_count: int
    predicted_commitment_count: int
    exact_match_case_count: int
    exact_match_accuracy: float
    missed_commitment_count: int
    miss_rate: float
    hallucinated_commitment_count: int
    hallucination_rate: float
    rejected_for_evidence_count: int
    cases: list[CaseEvaluation]


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalized_due(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def candidate_key(candidate: CommitmentCandidate) -> CandidateKey:
    return CandidateKey(
        operation=candidate.operation.value,
        task_class=candidate.task_class.value,
        summary=_normalized_text(candidate.summary),
        owner_id=candidate.owner_id,
        due_at=_normalized_due(candidate.due_at),
        target_commitment_id=candidate.target_commitment_id,
    )


def load_predictions(path: Path) -> dict[str, ExtractionEnvelope]:
    predictions: dict[str, ExtractionEnvelope] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            prediction = PredictionCase.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(f"invalid prediction at line {line_number}") from exc
        if prediction.case_id in predictions:
            raise ValueError(f"duplicate prediction case_id: {prediction.case_id}")
        predictions[prediction.case_id] = prediction.prediction
    return predictions


def evaluate_case(case: GoldCase, prediction: ExtractionEnvelope) -> CaseEvaluation:
    grounded = []
    rejected_for_evidence = 0
    for candidate in prediction.commitments:
        if evidence_rejection_code(case.event, candidate) is None:
            grounded.append(candidate)
        else:
            rejected_for_evidence += 1

    gold_counter = Counter(candidate_key(item) for item in case.expected.commitments)
    prediction_counter = Counter(candidate_key(item) for item in grounded)
    matched = sum((gold_counter & prediction_counter).values())
    missed = sum((gold_counter - prediction_counter).values())
    hallucinated = sum((prediction_counter - gold_counter).values())
    ambiguity_match = Counter(
        (item.field, item.source_message_ref) for item in case.expected.ambiguities
    ) == Counter((item.field, item.source_message_ref) for item in prediction.ambiguities)
    exact = missed == 0 and hallucinated == 0 and rejected_for_evidence == 0 and ambiguity_match
    return CaseEvaluation(
        case_id=case.case_id,
        exact_match=exact,
        gold_commitments=sum(gold_counter.values()),
        predicted_commitments=len(prediction.commitments),
        matched_commitments=matched,
        missed_commitments=missed,
        hallucinated_commitments=hallucinated + rejected_for_evidence,
        rejected_for_evidence=rejected_for_evidence,
    )


def evaluate(
    cases: list[GoldCase],
    predictions: dict[str, ExtractionEnvelope],
    *,
    dataset: str,
    predictor: str,
) -> EvaluationReport:
    expected_ids = {case.case_id for case in cases}
    unknown_ids = sorted(set(predictions) - expected_ids)
    if unknown_ids:
        raise ValueError(f"predictions contain unknown case IDs: {', '.join(unknown_ids)}")

    empty = ExtractionEnvelope()
    results = [evaluate_case(case, predictions.get(case.case_id, empty)) for case in cases]
    gold_total = sum(item.gold_commitments for item in results)
    predicted_total = sum(item.predicted_commitments for item in results)
    exact_total = sum(1 for item in results if item.exact_match)
    missed_total = sum(item.missed_commitments for item in results)
    hallucinated_total = sum(item.hallucinated_commitments for item in results)
    rejected_total = sum(item.rejected_for_evidence for item in results)
    return EvaluationReport(
        dataset=dataset,
        predictor=predictor,
        case_count=len(cases),
        gold_commitment_count=gold_total,
        predicted_commitment_count=predicted_total,
        exact_match_case_count=exact_total,
        exact_match_accuracy=exact_total / len(cases) if cases else 0.0,
        missed_commitment_count=missed_total,
        miss_rate=missed_total / gold_total if gold_total else 0.0,
        hallucinated_commitment_count=hallucinated_total,
        hallucination_rate=hallucinated_total / predicted_total if predicted_total else 0.0,
        rejected_for_evidence_count=rejected_total,
        cases=results,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate commitment extraction predictions.")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD_PATH)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--predictor", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cases = load_gold_cases(args.gold)
    report = evaluate(
        cases,
        load_predictions(args.predictions),
        dataset=str(args.gold),
        predictor=args.predictor,
    )
    serialized = report.model_dump_json(indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
