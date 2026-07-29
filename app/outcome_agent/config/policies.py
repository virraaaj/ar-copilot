"""Agent policy knobs derived from Settings (OUTCOME_AGENT_* / CHASE_* shim)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List


@dataclass(frozen=True)
class AgentPolicy:
    max_unanswered: int = 3
    max_missed_promises: int = 3
    max_postponements: int = 3
    max_commitment_days: int = 90
    grace_days: int = 2
    payment_verify_days: int = 3
    nudge_interval_days: int = 3
    min_days_between_emails: int = 3
    max_sends_per_tick: int = 10
    dry_run: bool = True
    to_address_allowlist: List[str] = None  # type: ignore[assignment]
    high_dollar_threshold: float = 50000.0
    composer_enabled: bool = False
    smart_escalation_enabled: bool = False

    def __post_init__(self) -> None:
        if self.to_address_allowlist is None:
            object.__setattr__(self, "to_address_allowlist", [])

    # Aliases for policy_knowledge / legacy ChaseConfig attribute names
    @property
    def max_nudges(self) -> int:
        return self.max_unanswered

    @property
    def max_missed_commitments(self) -> int:
        return self.max_missed_promises


def policy_from_settings(settings: Any) -> AgentPolicy:
    allowlist = []
    if hasattr(settings, "outcome_agent_to_address_allowlist"):
        allowlist = list(settings.outcome_agent_to_address_allowlist)
    elif hasattr(settings, "chase_to_address_allowlist"):
        allowlist = list(settings.chase_to_address_allowlist)
    return AgentPolicy(
        max_unanswered=int(
            getattr(settings, "OUTCOME_AGENT_MAX_NUDGES", None)
            or getattr(settings, "CHASE_MAX_NUDGES", 3)
        ),
        max_missed_promises=int(
            getattr(settings, "OUTCOME_AGENT_MAX_MISSED_COMMITMENTS", None)
            or getattr(settings, "CHASE_MAX_MISSED_COMMITMENTS", 3)
        ),
        max_postponements=int(
            getattr(settings, "OUTCOME_AGENT_MAX_POSTPONEMENTS", None)
            or getattr(settings, "CHASE_MAX_POSTPONEMENTS", 3)
        ),
        max_commitment_days=int(
            getattr(settings, "OUTCOME_AGENT_MAX_COMMITMENT_DAYS", None)
            or getattr(settings, "CHASE_MAX_COMMITMENT_DAYS", 90)
        ),
        grace_days=int(
            getattr(settings, "OUTCOME_AGENT_GRACE_DAYS", None)
            or getattr(settings, "CHASE_GRACE_DAYS", 2)
        ),
        payment_verify_days=int(
            getattr(settings, "OUTCOME_AGENT_PAYMENT_VERIFY_DAYS", None)
            or getattr(settings, "CHASE_PAYMENT_VERIFY_DAYS", 3)
        ),
        nudge_interval_days=int(
            getattr(settings, "OUTCOME_AGENT_NUDGE_INTERVAL_DAYS", None)
            or getattr(settings, "CHASE_NUDGE_INTERVAL_DAYS", 3)
        ),
        min_days_between_emails=max(
            1,
            int(
                (
                    getattr(settings, "OUTCOME_AGENT_MIN_HOURS_BETWEEN_TOUCHES", None)
                    or getattr(settings, "CHASE_MIN_HOURS_BETWEEN_TOUCHES", 72)
                )
                // 24
            ),
        ),
        max_sends_per_tick=int(
            getattr(settings, "OUTCOME_AGENT_MAX_SENDS_PER_TICK", None)
            or getattr(settings, "CHASE_MAX_SENDS_PER_TICK", 10)
        ),
        dry_run=bool(
            getattr(settings, "OUTCOME_AGENT_DRY_RUN", None)
            if getattr(settings, "OUTCOME_AGENT_DRY_RUN", None) is not None
            else getattr(settings, "CHASE_DRY_RUN", True)
        ),
        to_address_allowlist=allowlist,
        composer_enabled=bool(
            getattr(settings, "OUTCOME_AGENT_COMPOSER_ENABLED", None)
            or getattr(settings, "CHASE_COMPOSER_ENABLED", False)
        ),
        smart_escalation_enabled=bool(
            getattr(settings, "OUTCOME_AGENT_SMART_ESCALATION_ENABLED", None)
            or getattr(settings, "CHASE_SMART_ESCALATION_ENABLED", False)
        ),
    )
