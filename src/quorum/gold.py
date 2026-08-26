"""Gold-dataset loading and provenance validation."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from quorum.ledger import evidence_rejection_code
from quorum.models import (
    CanonicalMessageEvent,
    DataClassification,
    ExtractionEnvelope,
    StrictModel,
    TaskClass,
)

DEFAULT_GOLD_PATH = Path("data/eval/commitment_gold_v1.jsonl")


class GoldCase(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    case_id: Annotated[str, Field(pattern=r"^gold_[0-9]{3}$")]
    data_classification: Literal["synthetic"]
    event: CanonicalMessageEvent
    expected: ExtractionEnvelope
    tags: Annotated[list[str], Field(min_length=1)]


class GoldValidationError(ValueError):
    """Raised when a committed gold file violates its evidence contract."""


def load_gold_cases(path: Path = DEFAULT_GOLD_PATH) -> list[GoldCase]:
    cases: list[GoldCase] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = GoldCase.model_validate_json(line)
        except ValueError as exc:
            raise GoldValidationError(f"invalid gold case at line {line_number}") from exc
        if case.case_id in seen_ids:
            raise GoldValidationError(f"duplicate case_id: {case.case_id}")
        seen_ids.add(case.case_id)
        _validate_case(case)
        cases.append(case)
    if not cases:
        raise GoldValidationError("gold dataset is empty")
    return cases


def _validate_case(case: GoldCase) -> None:
    if case.event.data_classification is not DataClassification.SYNTHETIC:
        raise GoldValidationError(f"{case.case_id}: event must be synthetic")
    for candidate in case.expected.commitments:
        rejection = evidence_rejection_code(case.event, candidate)
        if rejection is not None:
            raise GoldValidationError(f"{case.case_id}: invalid evidence: {rejection}")
    for ambiguity in case.expected.ambiguities:
        if ambiguity.source_message_ref != case.event.source.source_message_ref:
            raise GoldValidationError(f"{case.case_id}: ambiguity source ref mismatch")


def summarize_cases(cases: Iterable[GoldCase]) -> dict[str, object]:
    materialized = list(cases)
    task_counts: Counter[str] = Counter()
    negative_cases = 0
    ambiguity_cases = 0
    commitment_count = 0
    for case in materialized:
        if not case.expected.commitments:
            negative_cases += 1
        if case.expected.ambiguities:
            ambiguity_cases += 1
        for candidate in case.expected.commitments:
            task_counts[candidate.task_class.value] += 1
            commitment_count += 1
    return {
        "case_count": len(materialized),
        "commitment_count": commitment_count,
        "negative_case_count": negative_cases,
        "ambiguity_case_count": ambiguity_cases,
        "task_class_counts": {task.value: task_counts[task.value] for task in TaskClass},
        "data_classification": "synthetic",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Quorum's commitment gold dataset.")
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_GOLD_PATH)
    args = parser.parse_args()
    cases = load_gold_cases(args.path)
    print(json.dumps(summarize_cases(cases), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
