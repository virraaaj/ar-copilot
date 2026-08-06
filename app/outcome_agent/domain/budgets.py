"""Autonomy budget accounting (P3)."""
from __future__ import annotations

from typing import Optional

from app.outcome_agent.domain.types import AutonomyBudget


def default_budget(
    max_unanswered: int = 3,
    max_postponements: int = 3,
    max_missed_promises: int = 3,
    min_days_between_emails: int = 3,
) -> AutonomyBudget:
    return AutonomyBudget(
        max_unanswered=max_unanswered,
        max_postponements=max_postponements,
        max_missed_promises=max_missed_promises,
        min_days_between_emails=min_days_between_emails,
    )


def consume_unanswered(budget: AutonomyBudget) -> AutonomyBudget:
    budget.unanswered_used += 1
    budget.relationship_risk = min(1.0, budget.relationship_risk + 0.15)
    return budget


def consume_postponement(budget: AutonomyBudget) -> AutonomyBudget:
    budget.postponements_used += 1
    return budget


def consume_miss(budget: AutonomyBudget) -> AutonomyBudget:
    budget.misses_used += 1
    budget.relationship_risk = min(1.0, budget.relationship_risk + 0.25)
    return budget


def reset_unanswered_on_reply(budget: AutonomyBudget) -> AutonomyBudget:
    budget.unanswered_used = 0
    return budget


def should_escalate_for_budget(budget: AutonomyBudget) -> bool:
    return budget.exhausted()


def hard_stop_reason(budget: Optional[AutonomyBudget]) -> Optional[str]:
    if budget is None:
        return None
    if budget.unanswered_used >= budget.max_unanswered:
        return f"Unanswered budget exhausted ({budget.unanswered_used}/{budget.max_unanswered})"
    if budget.misses_used >= budget.max_missed_promises:
        return f"Missed-promise budget exhausted ({budget.misses_used}/{budget.max_missed_promises})"
    return None
