"""Deterministic CaseState transitions — never LLM-owned."""
from __future__ import annotations

from typing import Optional, Set, Tuple

from app.outcome_agent.domain.types import CaseState, COLLECTIONS_SUPPRESSED_STATES

# (from_state, event) -> to_state
_TRANSITIONS: dict[Tuple[str, str], str] = {
    (CaseState.NOT_DUE.value, "became_due"): CaseState.DUE.value,
    (CaseState.DUE.value, "became_overdue"): CaseState.OVERDUE.value,
    (CaseState.DUE.value, "outreach"): CaseState.WAITING_FOR_CUSTOMER.value,
    (CaseState.OVERDUE.value, "outreach"): CaseState.WAITING_FOR_CUSTOMER.value,
    (CaseState.OUTREACH_READY.value, "outreach"): CaseState.WAITING_FOR_CUSTOMER.value,
    (CaseState.WAITING_FOR_CUSTOMER.value, "reply"): CaseState.CUSTOMER_RESPONDED.value,
    (CaseState.WAITING_FOR_CUSTOMER.value, "nudge"): CaseState.WAITING_FOR_CUSTOMER.value,
    (CaseState.CUSTOMER_RESPONDED.value, "blocker"): CaseState.BLOCKED.value,
    (CaseState.CUSTOMER_RESPONDED.value, "follow_up"): CaseState.FOLLOW_UP_SCHEDULED.value,
    (CaseState.CUSTOMER_RESPONDED.value, "promise"): CaseState.PROMISE_TO_PAY.value,
    (CaseState.CUSTOMER_RESPONDED.value, "dispute"): CaseState.DISPUTED.value,
    (CaseState.CUSTOMER_RESPONDED.value, "opt_out"): CaseState.SUPPRESSED.value,
    (CaseState.CUSTOMER_RESPONDED.value, "paid_claim"): CaseState.WAITING_FOR_CUSTOMER.value,
    (CaseState.CUSTOMER_RESPONDED.value, "clarify"): CaseState.WAITING_FOR_CUSTOMER.value,
    (CaseState.CUSTOMER_RESPONDED.value, "outreach"): CaseState.WAITING_FOR_CUSTOMER.value,
    (CaseState.BLOCKED.value, "blocker_cleared"): CaseState.OUTREACH_READY.value,
    (CaseState.BLOCKED.value, "promise"): CaseState.PROMISE_TO_PAY.value,
    (CaseState.BLOCKED.value, "follow_up"): CaseState.FOLLOW_UP_SCHEDULED.value,
    (CaseState.FOLLOW_UP_SCHEDULED.value, "due"): CaseState.OUTREACH_READY.value,
    (CaseState.FOLLOW_UP_SCHEDULED.value, "reply"): CaseState.CUSTOMER_RESPONDED.value,
    (CaseState.PROMISE_TO_PAY.value, "kept"): CaseState.PAID.value,
    (CaseState.PROMISE_TO_PAY.value, "missed"): CaseState.PROMISE_MISSED.value,
    (CaseState.PROMISE_MISSED.value, "outreach"): CaseState.WAITING_FOR_CUSTOMER.value,
    (CaseState.PROMISE_MISSED.value, "escalate"): CaseState.ESCALATION_REQUIRED.value,
    (CaseState.ESCALATION_REQUIRED.value, "handoff"): CaseState.ESCALATED_TO_HUMAN.value,
    (CaseState.WAITING_FOR_CUSTOMER.value, "escalate"): CaseState.ESCALATION_REQUIRED.value,
    (CaseState.OUTREACH_READY.value, "escalate"): CaseState.ESCALATION_REQUIRED.value,
    (CaseState.OVERDUE.value, "escalate"): CaseState.ESCALATION_REQUIRED.value,
}


def can_transition(from_state: str, event: str) -> bool:
    if from_state in COLLECTIONS_SUPPRESSED_STATES and event not in ("paid", "close", "resume"):
        return event == "paid" and from_state != CaseState.PAID.value
    return (from_state, event) in _TRANSITIONS or event in (
        "paid",
        "close",
        "pause",
        "resume",
        "suppress",
        "dispute",
    )


def transition(from_state: str, event: str) -> str:
    """Return next state. Hard rules override the table."""
    if event == "paid":
        return CaseState.PAID.value
    if event == "close":
        return CaseState.CLOSED.value
    if event == "pause":
        return CaseState.PAUSED.value
    if event == "suppress" or event == "opt_out":
        return CaseState.SUPPRESSED.value
    if event == "dispute":
        return CaseState.DISPUTED.value
    if event == "resume":
        if from_state == CaseState.PAUSED.value:
            return CaseState.OUTREACH_READY.value
        return from_state
    key = (from_state, event)
    if key in _TRANSITIONS:
        return _TRANSITIONS[key]
    return from_state


def collections_allowed(state: str) -> bool:
    return state not in COLLECTIONS_SUPPRESSED_STATES


def terminal(state: str) -> bool:
    return state in {
        CaseState.PAID.value,
        CaseState.CLOSED.value,
        CaseState.DISPUTED.value,
        CaseState.SUPPRESSED.value,
    }
