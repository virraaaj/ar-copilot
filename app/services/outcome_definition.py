"""
Explicit OutcomeDefinition config (long-horizon outcome agent spec §6.1).

Previously implicit: chase_machine.ChaseConfig already encodes the budget
thresholds (max_nudges, max_postponements, nudge_interval_days, ...) and
the state machine already encodes terminal states / acceptable-commitment
logic as code paths -- but neither was ever expressed as one named,
inspectable object matching the spec's own OutcomeDefinition shape.

This module doesn't replace ChaseConfig (chase_machine.py still needs its
own threshold values threaded through as plain function args -- that's
what keeps it pure and exhaustively testable). It's a read-only
DESCRIPTION of what those thresholds mean, for display (the Policy
Viewer, §6.18) and as the shape a second use case (§14.2) would need to
provide its own version of.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.services.chase_machine import ChaseConfig

ACCEPTABLE_COMMITMENTS = (
    "payment_date",
    "customer_follow_up_date",
    "blocker_resolution_date",
    "dispute_routed",
    "human_escalation_assigned",
)

# The spec's terminal_states are "paid", "closed", "disputed",
# "escalated_to_human", "cancelled" -- five labels for what our state
# machine expresses with two real terminal states plus one that's always
# a detour, not a distinct end state:
#   - "paid"                -> closed_paid
#   - "closed"/"cancelled"  -> closed_manual (an operator-initiated close
#                              covers both "we're done" and "never mind")
#   - "disputed"/           -> escalated. A confident dispute escalates
#     "escalated_to_human"     immediately (chase_machine.on_reply's
#                               dispute branch) -- there's no separate
#                               "disputed, waiting on someone" state,
#                               a human is in the loop right away, which
#                               is what both spec labels actually mean.
TERMINAL_STATES = ("closed_paid", "closed_manual", "escalated")


@dataclass(frozen=True)
class OutcomeDefinition:
    use_case: str = "collections"
    primary_outcome: str = "payment_received"
    acceptable_commitments: List[str] = field(default_factory=lambda: list(ACCEPTABLE_COMMITMENTS))
    terminal_states: List[str] = field(default_factory=lambda: list(TERMINAL_STATES))
    default_max_unanswered_outreach: int = 3
    default_min_days_between_emails: int = 2

    @classmethod
    def from_chase_config(cls, config: ChaseConfig) -> "OutcomeDefinition":
        """Derives the spec-shaped view from the chase engine's real,
        already-enforced ChaseConfig -- so anything that displays this
        (the Policy Viewer) always shows the actual thresholds in effect,
        not a second, driftable copy of them."""
        return cls(
            default_max_unanswered_outreach=config.max_nudges,
            default_min_days_between_emails=config.nudge_interval_days,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "use_case": self.use_case,
            "primary_outcome": self.primary_outcome,
            "acceptable_commitments": self.acceptable_commitments,
            "terminal_states": self.terminal_states,
            "default_max_unanswered_outreach": self.default_max_unanswered_outreach,
            "default_min_days_between_emails": self.default_min_days_between_emails,
        }
