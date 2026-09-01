"""Deterministic, visibly synthetic one-week replay for the public sandbox."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4

from pydantic import Field

from quorum.models import StrictModel


class ReplayMetrics(StrictModel):
    message_count: int = Field(ge=0)
    closed_decisions: int = Field(ge=0)
    decision_latency_p50_hours: float = Field(ge=0)
    interruption_count: int = Field(ge=0)
    undo_rate: float = Field(ge=0, le=1)


class ReplaySnapshot(StrictModel):
    replay_id: str
    dataset_id: str = "synthetic_week_v1"
    data_classification: str = "synthetic"
    baseline: ReplayMetrics
    quorum: ReplayMetrics
    interrupt_budget_limit_per_person: int = 2
    people_interrupted: int = 3
    max_interruptions_per_person: int = 2
    receipts: list[str]
    timeline: list[str]
    disclaimer: str = "Synthetic demonstration data; not a measured real-world outcome."


@dataclass(frozen=True, slots=True)
class ReplayStep:
    at_ms: int
    label: str


SYNTHETIC_TIMELINE = (
    ReplayStep(0, "214 synthetic messages loaded"),
    ReplayStep(900, "Commitment Ledger linked six decisions to source messages"),
    ReplayStep(1800, "Three reversible actions executed silently"),
    ReplayStep(2700, "Minimum quorum asked two people, not the whole group"),
    ReplayStep(3600, "Sunday summary prepared with six total interruptions"),
)


class ReplayStore:
    def __init__(self, *, max_runs: int = 100) -> None:
        if max_runs < 1:
            raise ValueError("max_runs must be positive")
        self._lock = Lock()
        self._max_runs = max_runs
        self._runs: OrderedDict[str, ReplaySnapshot] = OrderedDict()

    def start(self) -> ReplaySnapshot:
        replay_id = f"replay_{uuid4().hex[:16]}"
        snapshot = ReplaySnapshot(
            replay_id=replay_id,
            baseline=ReplayMetrics(
                message_count=214,
                closed_decisions=3,
                decision_latency_p50_hours=74.4,
                interruption_count=214,
                undo_rate=0.0,
            ),
            quorum=ReplayMetrics(
                message_count=8,
                closed_decisions=6,
                decision_latency_p50_hours=7.0,
                interruption_count=6,
                undo_rate=1 / 6,
            ),
            receipts=[
                "Tentative planning event created — Undo available for 24h",
                "Volunteer follow-up saved as a Gmail draft — not sent",
                "Supply count form opened — Undo available for 24h",
            ],
            timeline=[step.label for step in SYNTHETIC_TIMELINE],
        )
        with self._lock:
            self._runs[replay_id] = snapshot
            while len(self._runs) > self._max_runs:
                self._runs.popitem(last=False)
        return snapshot

    def get(self, replay_id: str) -> ReplaySnapshot | None:
        with self._lock:
            return self._runs.get(replay_id)
