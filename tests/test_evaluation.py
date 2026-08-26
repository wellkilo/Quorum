from __future__ import annotations

import unittest
from copy import deepcopy

from quorum.evaluation import evaluate, evaluate_case
from quorum.gold import DEFAULT_GOLD_PATH, load_gold_cases, summarize_cases
from quorum.models import ExtractionEnvelope


class GoldDatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_gold_cases(DEFAULT_GOLD_PATH)

    def test_dataset_has_exactly_fifty_synthetic_cases(self) -> None:
        summary = summarize_cases(self.cases)

        self.assertEqual(summary["case_count"], 50)
        self.assertEqual(summary["commitment_count"], 39)
        self.assertEqual(summary["negative_case_count"], 14)
        self.assertEqual(summary["ambiguity_case_count"], 4)
        self.assertTrue(all(count > 0 for count in summary["task_class_counts"].values()))

    def test_gold_predictions_score_perfectly(self) -> None:
        predictions = {case.case_id: case.expected for case in self.cases}

        report = evaluate(
            self.cases,
            predictions,
            dataset=str(DEFAULT_GOLD_PATH),
            predictor="gold-contract-self-check",
        )

        self.assertEqual(report.exact_match_accuracy, 1.0)
        self.assertEqual(report.miss_rate, 0.0)
        self.assertEqual(report.hallucination_rate, 0.0)

    def test_empty_baseline_exposes_full_commitment_miss_rate(self) -> None:
        report = evaluate(
            self.cases,
            {},
            dataset=str(DEFAULT_GOLD_PATH),
            predictor="empty-baseline",
        )

        self.assertEqual(report.exact_match_accuracy, 0.2)
        self.assertEqual(report.miss_rate, 1.0)
        self.assertEqual(report.hallucination_rate, 0.0)

    def test_ungrounded_prediction_counts_as_hallucination_and_rejection(self) -> None:
        case = self.cases[0]
        raw = deepcopy(case.expected.commitments[0].model_dump())
        raw["evidence"]["source_message_ref"] = "slack:C_SYN:1780000999.000100"
        prediction = ExtractionEnvelope.model_validate({"commitments": [raw]})

        result = evaluate_case(case, prediction)

        self.assertFalse(result.exact_match)
        self.assertEqual(result.missed_commitments, 1)
        self.assertEqual(result.hallucinated_commitments, 1)
        self.assertEqual(result.rejected_for_evidence, 1)


if __name__ == "__main__":
    unittest.main()
