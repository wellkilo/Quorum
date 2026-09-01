"""Cost-free synthetic smoke path for Quorum's three Slack interaction surfaces."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import Field

from quorum.models import (
    AutonomySnapshot,
    DataClassification,
    DecisionStatus,
    ExecutionProvider,
    ExecutionReceipt,
    ExecutionStatus,
    PolicyDecision,
    RiskAssessment,
    RiskTier,
    StrictModel,
    TaskClass,
    TimeoutDefault,
    WeeklySummary,
)
from quorum.replay import ReplaySnapshot
from quorum.slack import build_slack_notifier

DEFAULT_FIXTURE = Path(__file__).with_name("demo") / "synthetic-week.json"
DEMO_URL = "https://wellkilo.github.io/Quorum/?mode=slack-demo#replay"
UNDO_PREVIEW_URL = "https://wellkilo.github.io/Quorum/?mode=slack-demo&action=undo-preview#replay"


class SlackSurfaceSender(Protocol):
    def send_group_receipt(
        self,
        channel_id: str,
        receipt: ExecutionReceipt,
        *,
        data_classification: DataClassification | None = None,
    ) -> str: ...

    def send_private_question(
        self,
        participant_id: str,
        decision: PolicyDecision,
        *,
        data_classification: DataClassification | None = None,
    ) -> str: ...

    def send_weekly_summary(self, channel_id: str, summary: WeeklySummary) -> str: ...


class SlackSmokeResult(StrictModel):
    dataset_id: str
    data_classification: DataClassification
    messages_sent: int = Field(ge=0)
    group_receipt_ts: str
    private_question_ts: str
    weekly_summary_ts: str
    model_calls: int = 0
    execution_tool_calls: int = 0


def load_synthetic_snapshot(path: Path = DEFAULT_FIXTURE) -> ReplaySnapshot:
    """Load and verify the public fixture used by both Pages and the Slack smoke path."""

    snapshot = ReplaySnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    if snapshot.data_classification != DataClassification.SYNTHETIC.value:
        raise ValueError("Slack smoke requires a synthetic fixture")
    return snapshot


def run_slack_surface_smoke(
    sender: SlackSurfaceSender,
    *,
    channel_id: str,
    participant_id: str,
    snapshot: ReplaySnapshot,
    now: datetime | None = None,
) -> SlackSmokeResult:
    """Post exactly one receipt, one private question, and one weekly summary."""

    observed_at = (now or datetime.now(UTC)).astimezone(UTC)
    classification = DataClassification.SYNTHETIC
    receipt = ExecutionReceipt(
        organization_id="org_synthetic_demo",
        action_id="action_synthetic_calendar",
        tool_name="calendar_create_tentative_event",
        provider=ExecutionProvider.GOOGLE_CALENDAR,
        external_resource_id="synthetic_preview_only",
        external_url=DEMO_URL,
        status=ExecutionStatus.EXECUTED,
        reversible=True,
        executed_at=observed_at,
        undo_expires_at=observed_at + timedelta(hours=24),
        undo_url=UNDO_PREVIEW_URL,
    )
    decision = PolicyDecision(
        action_id="action_synthetic_approval",
        organization_id="org_synthetic_demo",
        requested_by_id="person_synthetic_requester",
        action_class=TaskClass.EVENT_DECISION,
        tool_name="calendar_create_tentative_event",
        arguments_fingerprint="a" * 64,
        risk=RiskAssessment(
            score=0,
            tier=RiskTier.LOW,
            reversibility_points=0,
            impact_radius_points=0,
            money_impact_points=0,
            reasons=[
                "reversibility:reversible=0",
                "impact_radius:individual=0",
                "money_impact:none=0",
            ],
        ),
        autonomy=AutonomySnapshot(),
        required_quorum=1,
        selected_decider_ids=[participant_id],
        status=DecisionStatus.AWAITING_APPROVAL,
        timeout_at=observed_at + timedelta(hours=24),
        timeout_default=TimeoutDefault.EXECUTE_AND_NOTIFY,
    )
    weekly = WeeklySummary(
        organization_id="org_synthetic_demo",
        week_ending=observed_at.date(),
        closed_decisions=snapshot.quorum.closed_decisions,
        decision_latency_p50_hours=snapshot.quorum.decision_latency_p50_hours,
        interruption_count=snapshot.quorum.interruption_count,
        people_interrupted=snapshot.people_interrupted,
        max_interruptions_per_person=snapshot.max_interruptions_per_person,
        interrupt_budget_limit_per_person=snapshot.interrupt_budget_limit_per_person,
        undo_rate=snapshot.quorum.undo_rate,
        data_classification=classification,
    )

    receipt_ts = sender.send_group_receipt(channel_id, receipt, data_classification=classification)
    question_ts = sender.send_private_question(
        participant_id, decision, data_classification=classification
    )
    summary_ts = sender.send_weekly_summary(channel_id, weekly)
    return SlackSmokeResult(
        dataset_id=snapshot.dataset_id,
        data_classification=classification,
        messages_sent=3,
        group_receipt_ts=receipt_ts,
        private_question_ts=question_ts,
        weekly_summary_ts=summary_ts,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview or post Quorum's three synthetic Slack interaction surfaces."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--confirm-live-posts", action="store_true")
    args = parser.parse_args(argv)
    snapshot = load_synthetic_snapshot(args.fixture)

    if not args.confirm_live_posts:
        print(
            json.dumps(
                {
                    "status": "preview",
                    "dataset_id": snapshot.dataset_id,
                    "data_classification": snapshot.data_classification,
                    "messages_to_send": 3,
                    "model_calls": 0,
                    "execution_tool_calls": 0,
                },
                sort_keys=True,
            )
        )
        return 0

    channel_id = os.environ.get("QUORUM_SLACK_DEMO_CHANNEL_ID", "").strip()
    participant_id = os.environ.get("QUORUM_SLACK_DEMO_PARTICIPANT_ID", "").strip()
    if not channel_id or not participant_id:
        parser.error(
            "live posts require QUORUM_SLACK_DEMO_CHANNEL_ID and QUORUM_SLACK_DEMO_PARTICIPANT_ID"
        )
    result = run_slack_surface_smoke(
        build_slack_notifier(),
        channel_id=channel_id,
        participant_id=participant_id,
        snapshot=snapshot,
    )
    print(result.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
