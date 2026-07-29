"""Outcome definition for collections agent."""
from __future__ import annotations

from typing import Any, Dict

from app.outcome_agent.config.policies import AgentPolicy
from app.outcome_agent.domain.types import PRINCIPLES


def outcome_definition(policy: AgentPolicy) -> Dict[str, Any]:
    return {
        "name": "collections_outcome",
        "primary_outcome": "Collect outstanding balance while preserving relationship",
        "success_signals": [
            "payment_posted",
            "active_commitment_with_date",
            "escalated_with_dossier",
            "dispute_routed",
        ],
        "budgets": {
            "max_unanswered": policy.max_unanswered,
            "max_missed_promises": policy.max_missed_promises,
            "max_postponements": policy.max_postponements,
            "min_days_between_emails": policy.min_days_between_emails,
        },
        "principles": PRINCIPLES,
        "hard_rules": [
            "balance_due <= 0 → paid; never outreach",
            "opted-out → suppress",
            "disputed → exit collections loop",
            "budget exhausted → escalation_required",
            "sim clock is sole time source when simulated",
        ],
    }
