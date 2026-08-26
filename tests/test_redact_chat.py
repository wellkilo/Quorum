from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.redact_chat import Redactor, redact_content, residual_counts


class RedactorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.redactor = Redactor(b"test-secret-key-for-quorum", ["Alice Chen", "陈小明"])

    def test_redacts_structured_slack_export_and_preserves_source_timestamp(self) -> None:
        source = {
            "user": "U12345678",
            "text": (
                "Alice Chen <@U12345678> email alice@example.com, "
                "call +86 138-0013-8000 from 10.0.0.5"
            ),
            "ts": "1770000000.000100",
            "profile": {
                "real_name": "Alice Chen",
                "phone": "13800138000",
            },
        }

        redacted = self.redactor.redact_value(source)

        self.assertRegex(redacted["user"], r"^<IDENTIFIER_[0-9a-f]{12}>$")
        self.assertNotIn("Alice Chen", json.dumps(redacted))
        self.assertNotIn("alice@example.com", redacted["text"])
        self.assertNotIn("138-0013-8000", redacted["text"])
        self.assertNotIn("10.0.0.5", redacted["text"])
        self.assertEqual(redacted["ts"], "1770000000.000100")

    def test_pseudonyms_are_stable_for_same_key_and_differ_for_other_key(self) -> None:
        first = self.redactor.pseudonym("PERSON", "Alice Chen")
        same = Redactor(b"test-secret-key-for-quorum").pseudonym("PERSON", "alice chen")
        other = Redactor(b"another-secret-key-for-quorum").pseudonym("PERSON", "Alice Chen")

        self.assertEqual(first, same)
        self.assertNotEqual(first, other)

    def test_jsonl_reports_bad_line_without_echoing_raw_content(self) -> None:
        with self.assertRaisesRegex(ValueError, r"invalid JSON on line 2") as context:
            redact_content('{"text": "safe"}\nsecret invalid line', "jsonl", self.redactor)
        self.assertNotIn("secret invalid line", str(context.exception))

    def test_csv_redacts_sensitive_column_and_message_text(self) -> None:
        source = "user,email,text\nU12345678,alice@example.com,Call 13800138000\n"

        result = redact_content(source, "csv", self.redactor)

        self.assertNotIn("U12345678", result)
        self.assertNotIn("alice@example.com", result)
        self.assertNotIn("13800138000", result)

    def test_residual_detector_is_clean_after_redaction(self) -> None:
        value = self.redactor.redact_text(
            "陈小明: mail me@example.org or call 13800138000 from 192.168.1.8"
        )
        self.assertFalse(any(residual_counts(value, ["陈小明"]).values()))

    def test_short_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 16 bytes"):
            Redactor(b"short")


class CommandLineTest(unittest.TestCase):
    def test_cli_writes_redacted_output_and_non_disclosing_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            source = directory / "slack.json"
            output = directory / "redacted.json"
            report = directory / "report.json"
            source.write_text(
                json.dumps(
                    {
                        "user": "U12345678",
                        "text": "Email alice@example.com or call 13800138000",
                    }
                ),
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["QUORUM_REDACTION_KEY"] = "local-test-secret-key"

            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "tools" / "redact_chat.py"),
                    "--input",
                    str(source),
                    "--output",
                    str(output),
                    "--report",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("alice@example.com", output.read_text(encoding="utf-8"))
            report_text = report.read_text(encoding="utf-8")
            self.assertNotIn("alice@example.com", report_text)
            report_data = json.loads(report_text)
            self.assertTrue(report_data["manual_review_required"])
            self.assertNotIn("source_file_name", report_data)
            self.assertRegex(report_data["source_file_sha256"], r"^[0-9a-f]{64}$")

    def test_cli_refuses_to_overwrite_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "messages.txt"
            report = Path(temporary_directory) / "report.json"
            source.write_text("safe text", encoding="utf-8")
            environment = os.environ.copy()
            environment["QUORUM_REDACTION_KEY"] = "local-test-secret-key"

            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "tools" / "redact_chat.py"),
                    "--input",
                    str(source),
                    "--output",
                    str(source),
                    "--report",
                    str(report),
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(source.read_text(encoding="utf-8"), "safe text")
            self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
