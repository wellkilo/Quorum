from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from quorum.models import (
    ActionRequest,
    AutonomyLevel,
    AutonomySnapshot,
    DecisionStatus,
    ImpactRadius,
    MoneyImpact,
    Reversibility,
    RiskTier,
    TaskClass,
    TimeoutDefault,
)
from quorum.policy import (
    APPROVALS_PER_PROMOTION,
    DECISION_TIMEOUT,
    approval_transition,
    assess_risk,
    plan_policy_decision,
    rejection_transition,
    undo_transition,
)

NOW = datetime(2026, 8, 26, 10, tzinfo=UTC)


def make_request(
    *,
    action_id: str = "action_test",
    reversibility: Reversibility = Reversibility.REVERSIBLE,
    impact_radius: ImpactRadius = ImpactRadius.INDIVIDUAL,
    money_impact: MoneyImpact = MoneyImpact.NONE,
    deciders: list[str] | None = None,
) -> ActionRequest:
    return ActionRequest(
        action_id=action_id,
        organization_id="org_test",
        requested_by_id="person_requester",
        action_class=TaskClass.EVENT_DECISION,
        tool_name="calendar_create_tentative_event",
        summary="Create a tentative planning event",
        reversibility=reversibility,
        impact_radius=impact_radius,
        money_impact=money_impact,
        candidate_decider_ids=deciders or ["person_a", "person_b", "person_c"],
        requested_at=NOW,
    )


class RiskPolicyTest(unittest.TestCase):
    def test_low_risk_action_scores_zero(self) -> None:
        risk = assess_risk(make_request())

        self.assertEqual(risk.score, 0)
        self.assertIs(risk.tier, RiskTier.LOW)

    def test_unbudgeted_money_is_always_high_risk(self) -> None:
        risk = assess_risk(make_request(money_impact=MoneyImpact.UNBUDGETED))

        self.assertIs(risk.tier, RiskTier.HIGH)
        self.assertEqual(risk.money_impact_points, 3)

    def test_high_risk_requires_two_people_even_at_max_autonomy(self) -> None:
        decision = plan_policy_decision(
            make_request(
                reversibility=Reversibility.IRREVERSIBLE,
                impact_radius=ImpactRadius.EXTERNAL,
            ),
            AutonomySnapshot(level=AutonomyLevel.AUTO_EXECUTE),
            {},
            now=NOW,
        )

        self.assertEqual(decision.required_quorum, 2)
        self.assertEqual(decision.selected_decider_ids, ["person_a", "person_b"])
        self.assertIs(decision.status, DecisionStatus.AWAITING_APPROVAL)
        self.assertIs(decision.timeout_default, TimeoutDefault.EXPIRE_WITHOUT_ACTION)

    def test_low_risk_at_notify_and_undo_is_silent(self) -> None:
        decision = plan_policy_decision(
            make_request(),
            AutonomySnapshot(level=AutonomyLevel.NOTIFY_AND_UNDO),
            {},
            now=NOW,
        )

        self.assertEqual(decision.required_quorum, 0)
        self.assertEqual(decision.selected_decider_ids, [])
        self.assertIs(decision.status, DecisionStatus.AUTHORIZED)
        self.assertIs(decision.timeout_default, TimeoutDefault.EXECUTE_AND_NOTIFY)

    def test_router_uses_minimum_available_quorum_and_24_hour_timeout(self) -> None:
        decision = plan_policy_decision(
            make_request(),
            AutonomySnapshot(),
            {"person_a": 2, "person_b": 1, "person_c": 0},
            now=NOW,
        )

        self.assertEqual(decision.selected_decider_ids, ["person_b"])
        self.assertEqual(decision.required_quorum, 1)
        self.assertEqual(decision.timeout_at, NOW + DECISION_TIMEOUT)

    def test_budget_exhaustion_defers_instead_of_interrupting(self) -> None:
        decision = plan_policy_decision(
            make_request(deciders=["person_a", "person_b"]),
            AutonomySnapshot(),
            {"person_a": 2, "person_b": 2},
            now=NOW,
        )

        self.assertIs(decision.status, DecisionStatus.DEFERRED_BUDGET)
        self.assertEqual(decision.selected_decider_ids, [])
        self.assertEqual([budget.remaining for budget in decision.budgets], [0, 0])


class AutonomyLadderTest(unittest.TestCase):
    def test_three_consecutive_approvals_promote_one_level(self) -> None:
        state = AutonomySnapshot()

        for _ in range(APPROVALS_PER_PROMOTION):
            state = approval_transition(state)

        self.assertIs(state.level, AutonomyLevel.SUGGEST)
        self.assertEqual(state.consecutive_approvals, 0)

    def test_rejection_resets_streak_and_downgrades(self) -> None:
        state = rejection_transition(
            AutonomySnapshot(
                level=AutonomyLevel.NOTIFY_AND_UNDO,
                consecutive_approvals=2,
            )
        )

        self.assertIs(state.level, AutonomyLevel.SUGGEST)
        self.assertEqual(state.consecutive_approvals, 0)
        self.assertEqual(state.rejection_count, 1)

    def test_undo_downgrades_but_never_below_ask_first(self) -> None:
        state = AutonomySnapshot(level=AutonomyLevel.ASK_FIRST)

        state = undo_transition(state)

        self.assertIs(state.level, AutonomyLevel.ASK_FIRST)
        self.assertEqual(state.undo_count, 1)

    def test_rolling_window_constant_is_seven_days(self) -> None:
        from quorum.policy import INTERRUPT_BUDGET_WINDOW

        self.assertEqual(INTERRUPT_BUDGET_WINDOW, timedelta(days=7))


if __name__ == "__main__":
    unittest.main()
