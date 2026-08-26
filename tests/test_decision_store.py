from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from quorum.database import DatabaseSettings, create_database_engine
from quorum.decision_store import ActionDecisionConflictError, DecisionPolicyStore
from quorum.models import (
    ActionRequest,
    AutonomyLevel,
    DecisionStatus,
    ImpactRadius,
    InterruptResolution,
    MoneyImpact,
    ParticipantResponse,
    Reversibility,
    TaskClass,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 26, 10, tzinfo=UTC)


def make_request(
    action_number: int,
    *,
    organization_id: str = "org_test",
    requested_at: datetime = NOW,
    deciders: list[str] | None = None,
    impact_radius: ImpactRadius = ImpactRadius.INDIVIDUAL,
    reversibility: Reversibility = Reversibility.REVERSIBLE,
) -> ActionRequest:
    return ActionRequest(
        action_id=f"action_{action_number}",
        organization_id=organization_id,
        requested_by_id="person_requester",
        action_class=TaskClass.EVENT_DECISION,
        tool_name="calendar_create_tentative_event",
        summary=f"Create tentative event {action_number}",
        reversibility=reversibility,
        impact_radius=impact_radius,
        money_impact=MoneyImpact.NONE,
        candidate_decider_ids=deciders or ["person_a", "person_b"],
        requested_at=requested_at,
    )


class DecisionPolicyStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        path = Path(self.temp_dir.name) / "policy.sqlite3"
        self.engine = create_database_engine(DatabaseSettings(url=f"sqlite+pysqlite:///{path}"))
        config = Config(PROJECT_ROOT / "alembic.ini")
        config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        self.store = DecisionPolicyStore(self.engine)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_decision_is_idempotent_and_interrupt_is_counted_once(self) -> None:
        request = make_request(1)

        first = self.store.decide(request, now=NOW)
        second = self.store.decide(request, now=NOW)

        self.assertEqual(first, second)
        self.assertIs(first.status, DecisionStatus.AWAITING_APPROVAL)
        self.assertEqual(first.selected_decider_ids, ["person_a"])
        self.assertEqual(
            self.store.interrupt_spend("org_test", ["person_a"], now=NOW),
            {"person_a": 1},
        )

    def test_reused_action_id_with_changed_request_is_rejected(self) -> None:
        self.store.decide(make_request(1), now=NOW)

        with self.assertRaises(ActionDecisionConflictError):
            self.store.decide(
                make_request(1).model_copy(update={"summary": "Changed action"}), now=NOW
            )

    def test_budget_routes_around_exhausted_person_then_defers(self) -> None:
        selected = []
        for action_number in range(1, 5):
            selected.append(
                self.store.decide(make_request(action_number), now=NOW).selected_decider_ids
            )

        deferred = self.store.decide(make_request(5), now=NOW)

        self.assertEqual(selected, [["person_a"], ["person_a"], ["person_b"], ["person_b"]])
        self.assertIs(deferred.status, DecisionStatus.DEFERRED_BUDGET)
        self.assertEqual(deferred.selected_decider_ids, [])

    def test_interrupts_older_than_seven_days_do_not_spend_current_budget(self) -> None:
        old = NOW - timedelta(days=8)
        self.store.decide(make_request(1, requested_at=old), now=old)
        self.store.decide(make_request(2, requested_at=old), now=old)

        spend = self.store.interrupt_spend("org_test", ["person_a"], now=NOW)

        self.assertEqual(spend, {"person_a": 0})

    def test_three_approved_actions_promote_autonomy(self) -> None:
        for action_number in range(1, 4):
            decision = self.store.decide(
                make_request(action_number, deciders=[f"person_{action_number}"]),
                now=NOW + timedelta(minutes=action_number),
            )
            status = self.store.resolve(
                "org_test",
                InterruptResolution(
                    action_id=decision.action_id,
                    responses=[
                        ParticipantResponse(
                            participant_id=decision.selected_decider_ids[0],
                            decision="approve",
                        )
                    ],
                ),
                now=NOW + timedelta(minutes=action_number, seconds=1),
            )
            self.assertIs(status, DecisionStatus.APPROVED)

        autonomy = self.store.autonomy_for("org_test", TaskClass.EVENT_DECISION)
        self.assertIs(autonomy.level, AutonomyLevel.SUGGEST)
        self.assertEqual(autonomy.consecutive_approvals, 0)

    def test_two_person_quorum_waits_for_both_and_rejection_is_final(self) -> None:
        decision = self.store.decide(
            make_request(
                1,
                impact_radius=ImpactRadius.EXTERNAL,
                reversibility=Reversibility.IRREVERSIBLE,
            ),
            now=NOW,
        )
        first = self.store.resolve(
            "org_test",
            InterruptResolution(
                action_id=decision.action_id,
                responses=[ParticipantResponse(participant_id="person_a", decision="approve")],
            ),
            now=NOW + timedelta(minutes=1),
        )
        second = self.store.resolve(
            "org_test",
            InterruptResolution(
                action_id=decision.action_id,
                responses=[ParticipantResponse(participant_id="person_b", decision="reject")],
            ),
            now=NOW + timedelta(minutes=2),
        )

        self.assertIs(first, DecisionStatus.AWAITING_APPROVAL)
        self.assertIs(second, DecisionStatus.REJECTED)

    def test_duplicate_approval_is_idempotent(self) -> None:
        decision = self.store.decide(make_request(1), now=NOW)
        resolution = InterruptResolution(
            action_id=decision.action_id,
            responses=[ParticipantResponse(participant_id="person_a", decision="approve")],
        )

        first = self.store.resolve("org_test", resolution, now=NOW + timedelta(minutes=1))
        second = self.store.resolve("org_test", resolution, now=NOW + timedelta(minutes=2))

        self.assertIs(first, DecisionStatus.APPROVED)
        self.assertIs(second, DecisionStatus.APPROVED)
        autonomy = self.store.autonomy_for("org_test", TaskClass.EVENT_DECISION)
        self.assertEqual(autonomy.consecutive_approvals, 1)
        with self.engine.connect() as connection:
            response_count = connection.execute(
                text(
                    "SELECT COUNT(*) FROM interrupt_events "
                    "WHERE organization_id=:org AND action_id=:action "
                    "AND event_type='approved'"
                ),
                {"org": "org_test", "action": decision.action_id},
            ).scalar_one()
        self.assertEqual(response_count, 1)
        with self.assertRaises(ValueError):
            self.store.resolve(
                "org_test",
                InterruptResolution(
                    action_id=decision.action_id,
                    responses=[ParticipantResponse(participant_id="person_a", decision="reject")],
                ),
                now=NOW + timedelta(minutes=3),
            )

    def test_decisions_and_interrupt_spend_are_tenant_isolated(self) -> None:
        first = self.store.decide(make_request(1, organization_id="org_first"), now=NOW)
        second = self.store.decide(make_request(1, organization_id="org_second"), now=NOW)

        self.assertEqual(first.organization_id, "org_first")
        self.assertEqual(second.organization_id, "org_second")
        self.assertEqual(
            self.store.interrupt_spend("org_first", ["person_a"], now=NOW),
            {"person_a": 1},
        )
        self.assertEqual(
            self.store.interrupt_spend("org_unrelated", ["person_a"], now=NOW),
            {"person_a": 0},
        )
        with self.assertRaises(KeyError):
            self.store.resolve(
                "org_unrelated",
                InterruptResolution(
                    action_id=first.action_id,
                    responses=[ParticipantResponse(participant_id="person_a", decision="approve")],
                ),
            )

    def test_low_risk_timeout_executes_with_notice(self) -> None:
        decision = self.store.decide(make_request(1), now=NOW)

        before = self.store.resolve_timeout(
            "org_test", decision.action_id, now=NOW + timedelta(hours=23)
        )
        after = self.store.resolve_timeout(
            "org_test", decision.action_id, now=NOW + timedelta(hours=24)
        )

        self.assertIs(before, DecisionStatus.AWAITING_APPROVAL)
        self.assertIs(after, DecisionStatus.AUTHORIZED)

    def test_high_risk_timeout_expires_and_records_audit_evidence(self) -> None:
        decision = self.store.decide(
            make_request(
                1,
                impact_radius=ImpactRadius.EXTERNAL,
                reversibility=Reversibility.IRREVERSIBLE,
            ),
            now=NOW,
        )

        status = self.store.resolve_timeout(
            "org_test", decision.action_id, now=NOW + timedelta(hours=24)
        )

        self.assertIs(status, DecisionStatus.EXPIRED)
        with self.engine.connect() as connection:
            expired = (
                connection.execute(
                    text(
                        "SELECT participant_id FROM interrupt_events "
                        "WHERE organization_id=:org AND action_id=:action "
                        "AND event_type='expired' ORDER BY participant_id"
                    ),
                    {"org": "org_test", "action": decision.action_id},
                )
                .scalars()
                .all()
            )
        self.assertEqual(expired, ["person_a", "person_b"])

    def test_late_high_risk_response_expires_and_only_marks_nonresponders(self) -> None:
        decision = self.store.decide(
            make_request(
                1,
                impact_radius=ImpactRadius.EXTERNAL,
                reversibility=Reversibility.IRREVERSIBLE,
            ),
            now=NOW,
        )
        first = self.store.resolve(
            "org_test",
            InterruptResolution(
                action_id=decision.action_id,
                responses=[ParticipantResponse(participant_id="person_a", decision="approve")],
            ),
            now=NOW + timedelta(hours=23),
        )

        late = self.store.resolve(
            "org_test",
            InterruptResolution(
                action_id=decision.action_id,
                responses=[ParticipantResponse(participant_id="person_b", decision="approve")],
            ),
            now=NOW + timedelta(hours=24),
        )

        self.assertIs(first, DecisionStatus.AWAITING_APPROVAL)
        self.assertIs(late, DecisionStatus.EXPIRED)
        with self.engine.connect() as connection:
            events = connection.execute(
                text(
                    "SELECT participant_id, event_type FROM interrupt_events "
                    "WHERE organization_id=:org AND action_id=:action "
                    "ORDER BY occurred_at, participant_id"
                ),
                {"org": "org_test", "action": decision.action_id},
            ).all()
        self.assertIn(("person_a", "approved"), events)
        self.assertIn(("person_b", "expired"), events)
        self.assertNotIn(("person_a", "expired"), events)
        self.assertNotIn(("person_b", "approved"), events)

    def test_undo_is_idempotent_and_downgrades_only_executable_actions(self) -> None:
        for action_number in range(1, 4):
            decision = self.store.decide(
                make_request(action_number, deciders=[f"person_{action_number}"]),
                now=NOW + timedelta(minutes=action_number),
            )
            self.store.resolve(
                "org_test",
                InterruptResolution(
                    action_id=decision.action_id,
                    responses=[
                        ParticipantResponse(
                            participant_id=decision.selected_decider_ids[0],
                            decision="approve",
                        )
                    ],
                ),
                now=NOW + timedelta(minutes=action_number, seconds=1),
            )

        self.assertIs(
            self.store.autonomy_for("org_test", TaskClass.EVENT_DECISION).level,
            AutonomyLevel.SUGGEST,
        )
        self.store.record_undo("org_test", "action_3", now=NOW + timedelta(hours=1))
        self.store.record_undo("org_test", "action_3", now=NOW + timedelta(hours=2))

        autonomy = self.store.autonomy_for("org_test", TaskClass.EVENT_DECISION)
        self.assertIs(autonomy.level, AutonomyLevel.ASK_FIRST)
        self.assertEqual(autonomy.undo_count, 1)
        pending = self.store.decide(make_request(4, deciders=["person_pending"]), now=NOW)
        with self.assertRaises(ValueError):
            self.store.record_undo("org_test", pending.action_id, now=NOW)

        irreversible = self.store.decide(
            make_request(
                5,
                deciders=["person_irreversible_a", "person_irreversible_b"],
                impact_radius=ImpactRadius.EXTERNAL,
                reversibility=Reversibility.IRREVERSIBLE,
            ),
            now=NOW,
        )
        self.store.resolve(
            "org_test",
            InterruptResolution(
                action_id=irreversible.action_id,
                responses=[
                    ParticipantResponse(participant_id=participant_id, decision="approve")
                    for participant_id in irreversible.selected_decider_ids
                ],
            ),
            now=NOW + timedelta(minutes=1),
        )
        with self.assertRaises(ValueError):
            self.store.record_undo("org_test", irreversible.action_id, now=NOW)

    def test_interrupt_audit_is_database_append_only(self) -> None:
        self.store.decide(make_request(1), now=NOW)

        for statement in (
            "UPDATE interrupt_events SET event_type='expired'",
            "DELETE FROM interrupt_events",
        ):
            with self.assertRaises(DatabaseError), self.engine.begin() as connection:
                connection.execute(text(statement))


if __name__ == "__main__":
    unittest.main()
