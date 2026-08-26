"""Deterministic risk, autonomy, quorum, and interruption-budget policy."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from quorum.models import (
    ActionRequest,
    AutonomyLevel,
    AutonomySnapshot,
    DecisionStatus,
    ImpactRadius,
    InterruptBudgetSnapshot,
    MoneyImpact,
    PolicyDecision,
    Reversibility,
    RiskAssessment,
    RiskTier,
    TimeoutDefault,
)

INTERRUPT_BUDGET_LIMIT = 2
INTERRUPT_BUDGET_WINDOW = timedelta(days=7)
DECISION_TIMEOUT = timedelta(hours=24)
APPROVALS_PER_PROMOTION = 3

_REVERSIBILITY_POINTS = {
    Reversibility.REVERSIBLE: 0,
    Reversibility.COMPENSATABLE: 1,
    Reversibility.IRREVERSIBLE: 3,
}
_IMPACT_POINTS = {
    ImpactRadius.INDIVIDUAL: 0,
    ImpactRadius.GROUP: 1,
    ImpactRadius.EXTERNAL: 3,
}
_MONEY_POINTS = {
    MoneyImpact.NONE: 0,
    MoneyImpact.BUDGETED: 1,
    MoneyImpact.UNBUDGETED: 3,
}


def fingerprint_action_arguments(arguments: Mapping[str, object]) -> str:
    """Hash canonical JSON so a model cannot change approved tool arguments at execution time."""

    normalized = {key: value for key, value in arguments.items() if value is not None}
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def assess_risk(request: ActionRequest) -> RiskAssessment:
    """Score only declared, typed action properties; model prose cannot override this result."""

    reversibility_points = _REVERSIBILITY_POINTS[request.reversibility]
    impact_points = _IMPACT_POINTS[request.impact_radius]
    money_points = _MONEY_POINTS[request.money_impact]
    score = reversibility_points + impact_points + money_points
    if (
        request.reversibility is Reversibility.IRREVERSIBLE
        or request.money_impact is MoneyImpact.UNBUDGETED
        or score >= 4
    ):
        tier = RiskTier.HIGH
    elif score >= 2:
        tier = RiskTier.MEDIUM
    else:
        tier = RiskTier.LOW
    return RiskAssessment(
        score=score,
        tier=tier,
        reversibility_points=reversibility_points,
        impact_radius_points=impact_points,
        money_impact_points=money_points,
        reasons=[
            f"reversibility:{request.reversibility.value}={reversibility_points}",
            f"impact_radius:{request.impact_radius.value}={impact_points}",
            f"money_impact:{request.money_impact.value}={money_points}",
        ],
    )


def required_quorum_for(
    request: ActionRequest, risk: RiskAssessment, autonomy: AutonomySnapshot
) -> int:
    """Return the smallest safe approval count for the current trust and risk."""

    if risk.tier is RiskTier.HIGH:
        return 2
    if risk.tier is RiskTier.MEDIUM:
        if (
            autonomy.level is AutonomyLevel.AUTO_EXECUTE
            and request.reversibility is not Reversibility.IRREVERSIBLE
            and request.money_impact is MoneyImpact.NONE
        ):
            return 0
        return 1
    if autonomy.level >= AutonomyLevel.NOTIFY_AND_UNDO:
        return 0
    return 1


def plan_policy_decision(
    request: ActionRequest,
    autonomy: AutonomySnapshot,
    interrupt_spend: Mapping[str, int],
    *,
    now: datetime | None = None,
) -> PolicyDecision:
    """Route an action without exceeding any person's rolling interruption budget."""

    evaluated_at = (now or datetime.now(UTC)).astimezone(UTC)
    risk = assess_risk(request)
    required_quorum = required_quorum_for(request, risk, autonomy)
    budgets = [
        InterruptBudgetSnapshot(
            participant_id=participant_id,
            spent=interrupt_spend.get(participant_id, 0),
            limit=INTERRUPT_BUDGET_LIMIT,
        )
        for participant_id in request.candidate_decider_ids
    ]
    available = [budget.participant_id for budget in budgets if budget.remaining > 0]

    if required_quorum == 0:
        status = DecisionStatus.AUTHORIZED
        selected: list[str] = []
        timeout_at = None
    elif len(available) >= required_quorum:
        status = DecisionStatus.AWAITING_APPROVAL
        selected = available[:required_quorum]
        timeout_at = evaluated_at + DECISION_TIMEOUT
    else:
        status = DecisionStatus.DEFERRED_BUDGET
        selected = []
        timeout_at = evaluated_at + DECISION_TIMEOUT

    return PolicyDecision(
        action_id=request.action_id,
        organization_id=request.organization_id,
        requested_by_id=request.requested_by_id,
        action_class=request.action_class,
        tool_name=request.tool_name,
        arguments_fingerprint=fingerprint_action_arguments(request.action_arguments),
        risk=risk,
        autonomy=autonomy,
        required_quorum=required_quorum,
        selected_decider_ids=selected,
        budgets=budgets,
        status=status,
        timeout_at=timeout_at,
        timeout_default=(
            TimeoutDefault.EXECUTE_AND_NOTIFY
            if risk.tier is RiskTier.LOW
            else TimeoutDefault.EXPIRE_WITHOUT_ACTION
        ),
    )


def approval_transition(snapshot: AutonomySnapshot) -> AutonomySnapshot:
    approvals = snapshot.consecutive_approvals + 1
    level = snapshot.level
    if approvals >= APPROVALS_PER_PROMOTION and level < AutonomyLevel.AUTO_EXECUTE:
        level = AutonomyLevel(level + 1)
        approvals = 0
    return snapshot.model_copy(update={"level": level, "consecutive_approvals": approvals})


def rejection_transition(snapshot: AutonomySnapshot) -> AutonomySnapshot:
    return snapshot.model_copy(
        update={
            "level": AutonomyLevel(max(snapshot.level - 1, AutonomyLevel.ASK_FIRST)),
            "consecutive_approvals": 0,
            "rejection_count": snapshot.rejection_count + 1,
        }
    )


def undo_transition(snapshot: AutonomySnapshot) -> AutonomySnapshot:
    return snapshot.model_copy(
        update={
            "level": AutonomyLevel(max(snapshot.level - 1, AutonomyLevel.ASK_FIRST)),
            "consecutive_approvals": 0,
            "undo_count": snapshot.undo_count + 1,
        }
    )
