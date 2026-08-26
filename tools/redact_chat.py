#!/usr/bin/env python3
"""Locally redact chat exports without retaining raw identifiers.

Supported formats are JSON, JSONL/NDJSON, CSV, and plain text. Pseudonyms are
stable only for the same secret key. The report contains counts, never samples.
This tool reduces disclosure risk; it does not replace consent or human review.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

REDACTION_KEY_ENV = "QUORUM_REDACTION_KEY"
MINIMUM_KEY_LENGTH = 16

SENSITIVE_FIELD_KINDS: Mapping[str, str] = {
    "email": "EMAIL",
    "email_address": "EMAIL",
    "mail": "EMAIL",
    "phone": "PHONE",
    "phone_number": "PHONE",
    "mobile": "PHONE",
    "real_name": "PERSON",
    "real_name_normalized": "PERSON",
    "display_name": "PERSON",
    "display_name_normalized": "PERSON",
    "first_name": "PERSON",
    "last_name": "PERSON",
    "address": "ADDRESS",
    "home_address": "ADDRESS",
    "ip_address": "IP",
    "ip": "IP",
}

IDENTIFIER_FIELDS = {
    "user",
    "user_id",
    "actor_id",
    "creator_id",
    "team_id",
    "workspace_id",
    "channel_id",
}

SLACK_MENTION_RE = re.compile(r"<@([UW][A-Z0-9]{2,})>")
SLACK_ID_RE = re.compile(r"(?<![A-Z0-9])([UCTW][A-Z0-9]{7,})(?![A-Z0-9])")
EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.I)
IPV4_RE = re.compile(
    r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
)
PHONE_RE = re.compile(
    r"(?<![\d])(?:\+?86[- ]?)?1[3-9]\d(?:[- ]?\d){8}(?![\d])"
    r"|(?<![\d])\+[1-9]\d{0,2}(?:[- .]?\d){7,12}(?![\d])"
)


def canonical_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


@dataclass
class RedactionStats:
    replacements: Counter[str] = field(default_factory=Counter)
    values_processed: int = 0

    def record(self, kind: str) -> None:
        self.replacements[kind] += 1

    @property
    def total_replacements(self) -> int:
        return sum(self.replacements.values())


class Redactor:
    def __init__(self, key: bytes, names: Iterable[str] = ()) -> None:
        if len(key) < MINIMUM_KEY_LENGTH:
            raise ValueError(
                f"redaction key must be at least {MINIMUM_KEY_LENGTH} bytes"
            )
        self._key = key
        self.stats = RedactionStats()
        unique_names = {name.strip() for name in names if name.strip()}
        self._names = sorted(unique_names, key=len, reverse=True)
        self._name_patterns = [
            re.compile(re.escape(name), re.IGNORECASE) for name in self._names
        ]

    def pseudonym(self, kind: str, value: str) -> str:
        normalized = value.strip().casefold().encode("utf-8")
        digest = hmac.new(
            self._key, kind.encode("ascii") + b"\0" + normalized, hashlib.sha256
        ).hexdigest()[:12]
        return f"<{kind}_{digest}>"

    def _replace_match(self, kind: str) -> Callable[[re.Match[str]], str]:
        def replace(match: re.Match[str]) -> str:
            self.stats.record(kind)
            return self.pseudonym(kind, match.group(0))

        return replace

    def redact_text(self, value: str) -> str:
        self.stats.values_processed += 1
        result = SLACK_MENTION_RE.sub(
            lambda match: self._replace_identifier_match("SLACK_USER", match, 1),
            value,
        )
        result = EMAIL_RE.sub(self._replace_match("EMAIL"), result)
        result = PHONE_RE.sub(self._replace_match("PHONE"), result)
        result = IPV4_RE.sub(self._replace_match("IP"), result)
        result = SLACK_ID_RE.sub(self._replace_match("SLACK_ID"), result)
        for name, pattern in zip(self._names, self._name_patterns):
            result, count = pattern.subn(self.pseudonym("PERSON", name), result)
            if count:
                self.stats.replacements["PERSON"] += count
        return result

    def _replace_identifier_match(
        self, kind: str, match: re.Match[str], group: int
    ) -> str:
        self.stats.record(kind)
        return self.pseudonym(kind, match.group(group))

    def redact_field_value(self, field: str, value: Any) -> Any:
        normalized_field = canonical_field_name(field)
        if value is None:
            return None
        if normalized_field in SENSITIVE_FIELD_KINDS:
            kind = SENSITIVE_FIELD_KINDS[normalized_field]
            self.stats.record(kind)
            return self.pseudonym(kind, str(value))
        if normalized_field in IDENTIFIER_FIELDS and isinstance(value, (str, int)):
            self.stats.record("IDENTIFIER")
            return self.pseudonym("IDENTIFIER", str(value))
        return self.redact_value(value)

    def redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        if isinstance(value, dict):
            return {
                key: self.redact_field_value(str(key), item)
                for key, item in value.items()
            }
        return value


def read_names(path: Path | None) -> list[str]:
    if path is None:
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        candidate = line.strip()
        if candidate and not candidate.startswith("#"):
            names.append(candidate)
    return names


def detect_format(path: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    suffix = path.suffix.casefold()
    formats = {
        ".json": "json",
        ".jsonl": "jsonl",
        ".ndjson": "jsonl",
        ".csv": "csv",
        ".txt": "text",
        ".log": "text",
    }
    try:
        return formats[suffix]
    except KeyError as error:
        raise ValueError(
            f"cannot infer format from {path.name}; pass --format explicitly"
        ) from error


def redact_content(raw: str, source_format: str, redactor: Redactor) -> str:
    if source_format == "json":
        value = json.loads(raw)
        return json.dumps(
            redactor.redact_value(value), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
    if source_format == "jsonl":
        output = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON on line {line_number}: {error.msg}") from error
            output.append(
                json.dumps(redactor.redact_value(value), ensure_ascii=False, sort_keys=True)
            )
        return "\n".join(output) + ("\n" if output else "")
    if source_format == "csv":
        reader = csv.DictReader(io.StringIO(raw))
        if reader.fieldnames is None:
            raise ValueError("CSV input must contain a header row")
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=reader.fieldnames)
        writer.writeheader()
        for row in reader:
            writer.writerow(
                {
                    field: redactor.redact_field_value(field, value)
                    for field, value in row.items()
                }
            )
        return buffer.getvalue()
    if source_format == "text":
        return redactor.redact_text(raw)
    raise ValueError(f"unsupported format: {source_format}")


def residual_counts(value: str, names: Sequence[str]) -> dict[str, int]:
    counts = {
        "email": len(EMAIL_RE.findall(value)),
        "phone": len(PHONE_RE.findall(value)),
        "ipv4": len(IPV4_RE.findall(value)),
        "slack_mention": len(SLACK_MENTION_RE.findall(value)),
        "slack_id": len(SLACK_ID_RE.findall(value)),
    }
    counts["provided_name"] = sum(
        len(re.findall(re.escape(name), value, re.IGNORECASE))
        for name in names
        if name
    )
    return counts


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def build_report(
    input_path: Path,
    source_format: str,
    redactor: Redactor,
    residuals: Mapping[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "source_file_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "source_format": source_format,
        "data_classification": "redacted-private",
        "values_processed": redactor.stats.values_processed,
        "replacement_counts": dict(sorted(redactor.stats.replacements.items())),
        "total_replacements": redactor.stats.total_replacements,
        "residual_detector_counts": dict(residuals),
        "manual_review_required": True,
        "limitations": [
            "Natural-language names require a supplied name file and human review.",
            "Context-dependent secrets and addresses may not match pattern detectors.",
            "A clean residual scan is not proof that the output contains no personal data.",
        ],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--format", choices=("json", "jsonl", "csv", "text"), default=None
    )
    parser.add_argument(
        "--name-file",
        type=Path,
        help="UTF-8 file with one name or organization-specific term per line",
    )
    parser.add_argument(
        "--allow-residuals",
        action="store_true",
        help="Write output even if a built-in detector still sees likely PII",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    report_path = args.report.resolve()
    if output_path == input_path or report_path == input_path:
        raise ValueError("output and report paths must not overwrite the input")
    if output_path == report_path:
        raise ValueError("output and report paths must be different")

    raw_key = os.environ.get(REDACTION_KEY_ENV, "")
    if len(raw_key.encode("utf-8")) < MINIMUM_KEY_LENGTH:
        raise ValueError(
            f"set {REDACTION_KEY_ENV} to a local secret of at least "
            f"{MINIMUM_KEY_LENGTH} bytes"
        )

    names = read_names(args.name_file)
    source_format = detect_format(input_path, args.format)
    raw = input_path.read_text(encoding="utf-8-sig")
    redactor = Redactor(raw_key.encode("utf-8"), names)
    redacted = redact_content(raw, source_format, redactor)
    residuals = residual_counts(redacted, names)
    report = build_report(input_path, source_format, redactor, residuals)

    if any(residuals.values()) and not args.allow_residuals:
        summary = ", ".join(f"{name}={count}" for name, count in residuals.items() if count)
        raise ValueError(
            "residual PII detectors matched the redacted output; no files were written "
            f"({summary}). Review the input rules or use --allow-residuals only after "
            "manual inspection."
        )

    atomic_write_text(output_path, redacted)
    atomic_write_text(
        report_path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output_path),
                "report": str(report_path),
                "total_replacements": redactor.stats.total_replacements,
                "manual_review_required": True,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
