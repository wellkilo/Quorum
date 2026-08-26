"""PII-safe OpenTelemetry attributes for Quorum's consequential path."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace

_ALLOWED_ATTRIBUTE_KEYS = frozenset(
    {
        "quorum.organization_id",
        "quorum.session_id",
        "quorum.graph_node",
        "quorum.policy_outcome",
        "quorum.interrupt_count",
        "quorum.action_id",
        "quorum.data_classification",
        "quorum.replay_id",
    }
)
_STRANDS_REDACTION_TOKEN = "gen_ai_unredacted_attributes="


def configure_strands_trace_redaction() -> None:
    """Enable Strands' built-in redaction for prompts, tool arguments, and results."""

    current = [
        value.strip()
        for value in os.environ.get("OTEL_SEMCONV_STABILITY_OPT_IN", "").split(",")
        if value.strip()
    ]
    if not any(value.startswith("gen_ai_unredacted_attributes=") for value in current):
        current.append(_STRANDS_REDACTION_TOKEN)
    os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = ",".join(current)


def safe_trace_attributes(attributes: Mapping[str, object]) -> dict[str, str | int | bool | float]:
    """Return only bounded, pre-approved scalar correlation attributes."""

    safe: dict[str, str | int | bool | float] = {}
    for key, value in attributes.items():
        if key not in _ALLOWED_ATTRIBUTE_KEYS:
            raise ValueError(f"trace attribute is not allow-listed: {key}")
        if isinstance(value, str):
            if len(value) > 200:
                raise ValueError(f"trace attribute exceeds 200 characters: {key}")
            safe[key] = value
        elif isinstance(value, (int, bool, float)):
            safe[key] = value
        else:
            raise TypeError(f"trace attribute must be scalar: {key}")
    return safe


@contextmanager
def traced_operation(name: str, attributes: Mapping[str, object]) -> Iterator[Any]:
    """Create one span without recording prompts, messages, tokens, or provider payloads."""

    tracer = trace.get_tracer("quorum")
    with tracer.start_as_current_span(name) as span:
        for key, value in safe_trace_attributes(attributes).items():
            span.set_attribute(key, value)
        yield span
