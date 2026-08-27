from __future__ import annotations

import re
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
VIDEO_DIRECTORY = REPOSITORY_ROOT / "docs" / "video"
TIMESTAMP = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}\.\d{3}) --> "
    r"(?P<end>\d{2}:\d{2}:\d{2}\.\d{3})"
)


def _seconds(timestamp: str) -> float:
    hours, minutes, seconds = timestamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


class VideoAssetContractTest(unittest.TestCase):
    def test_caption_track_is_ordered_and_under_five_minutes(self) -> None:
        content = (VIDEO_DIRECTORY / "quorum-demo.en.vtt").read_text(encoding="utf-8")
        cues = list(TIMESTAMP.finditer(content))

        self.assertTrue(content.startswith("WEBVTT\n"))
        self.assertGreaterEqual(len(cues), 20)
        previous_end = 0.0
        for cue in cues:
            start = _seconds(cue.group("start"))
            end = _seconds(cue.group("end"))
            self.assertGreater(end, start)
            self.assertGreaterEqual(start, previous_end)
            previous_end = end
        self.assertLessEqual(previous_end, 300.0)
        self.assertEqual(previous_end, 290.0)

    def test_script_contains_required_visible_evidence_and_boundaries(self) -> None:
        content = (VIDEO_DIRECTORY / "script.md").read_text(encoding="utf-8")
        normalized_content = " ".join(content.split())

        for required in (
            "214 messages",
            "six times",
            "74.4 hours to seven hours",
            "two interruptions per rolling week",
            "five-node Strands Graph",
            "Strands Swarm",
            "native hook interrupt",
            "AgentCore Runtime",
            "AgentCore Memory",
            "AgentCore Gateway",
            "PostgreSQL",
            "PII-safe OpenTelemetry",
            "50-case synthetic gold set",
            "not measured community impact",
            "actual AWS Builder ID",
        ):
            self.assertIn(required, normalized_content)

    def test_storyboard_requires_synthetic_labels_and_rejects_fake_evidence(self) -> None:
        content = (VIDEO_DIRECTORY / "storyboard.md").read_text(encoding="utf-8")

        self.assertIn("Public static replay · synthetic data only", content)
        self.assertIn("No consented pilot quote yet — no quote fabricated", content)
        self.assertIn("Do not simulate a cloud", content)
        self.assertIn("actual AWS Builder ID", content)


if __name__ == "__main__":
    unittest.main()
