"""Regression coverage for two 2026-09-03 fixes to apply_reply_signal
(app/outcome_agent/loop/signal_ingestion.py):

Change 1 -- a hostile reply that also names a payment date ("This is
ridiculous. We'll pay on the 15th.") must not lose that date just because
hostility routes to escalation. reply_interpreter._interpret_core checks
hostility before payment_date, so the hostile branch previously returned
early without ever extracting a date, and signal_ingestion's hostile branch
(added earlier the same day) only escalated -- the commitment was silently
dropped. Fixed by extracting the date in the hostile branch too (reusing
_extract_date) and recording it as a normal active payment_date commitment
in signal_ingestion, while escalation still wins for routing (state stays
ESCALATION_REQUIRED, never promise_to_pay).

Change 2 -- a disputed invoice is the customer's CLAIM, not a world-truth
fact. The dispute branch used to write world.status = "disputed" straight
from an unverified customer message, contradicting this codebase's own P1
principle (world_model.py:1) and inconsistent with how a paid-claim is
handled (dialogue.customer_claimed_paid, never world.status = "paid").
Fixed by recording dialogue.customer_claimed_disputed instead, while
escalation (goals.choose_objective) and send-suppression
(guardrails.check_before_send) still fire because both already gate on
case["state"] == "disputed" (a hard rule in state_machine.transition), not
just world.status.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.outcome_agent.domain.goals import build_goal_stack, choose_objective
from app.outcome_agent.domain.types import AutonomyBudget, Uncertainty
from app.outcome_agent.domain.dialogue_model import DialogueSnapshot
from app.outcome_agent.domain.world_model import WorldSnapshot
from app.outcome_agent.loop.guardrails import check_before_send
from app.outcome_agent.loop.signal_ingestion import apply_reply_signal

CONFIDENT = Uncertainty(confidence=1.0, needs_clarification=False, unclear_fields=[], note="")


class _FakeLedger:
    """Minimal stand-in for EventLedger -- apply_reply_signal only calls
    .append(), and none of these tests need to inspect what was logged."""

    async def append(self, *args, **kwargs) -> None:
        return None


def _make_case() -> dict:
    return {
        "id": "case-1",
        "case_id": "case-1",
        "invoice_no": "INV-1",
        "state": "waiting_for_customer",
        "target": "customer",
        "world": {
            "invoice_no": "INV-1",
            "case_id": "case-1",
            "balance_due": 1000.0,
            "status": "open",
        },
        "dialogue": {},
        "budget": {},
        "commitments": [],
        "blockers": [],
    }


def _active_payment_date_commitments(case: dict) -> list:
    return [
        c
        for c in case["commitments"]
        if c.get("type") == "payment_date" and c.get("status") == "active"
    ]


# ---------------------------------------------------------------------------
# Change 1: hostile + date
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hostile_reply_with_date_escalates_and_keeps_the_commitment():
    case = _make_case()
    interp = await apply_reply_signal(
        case,
        "This is ridiculous. We'll pay on September 15th.",
        _FakeLedger(),
        now=datetime(2026, 9, 3),
    )
    assert interp.reply_type == "hostile"
    # Escalation wins for routing.
    assert case["state"] == "escalation_required"
    # But the payment date is not lost.
    active = _active_payment_date_commitments(case)
    assert len(active) == 1
    assert active[0]["date"] == "2026-09-15"
    assert active[0]["owner"] == "customer"
    assert active[0]["source"] == "customer"
    assert case["dialogue"]["customer_promised_date"] == "2026-09-15"


@pytest.mark.asyncio
async def test_hostile_reply_without_date_escalates_and_invents_nothing():
    case = _make_case()
    interp = await apply_reply_signal(
        case, "This is ridiculous, we will sue you.", _FakeLedger(), now=datetime(2026, 9, 3)
    )
    assert interp.reply_type == "hostile"
    assert case["state"] == "escalation_required"
    assert case["commitments"] == []
    assert case["dialogue"].get("customer_promised_date") is None


# ---------------------------------------------------------------------------
# Change 2: dispute is a claim, not world truth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispute_reply_records_claim_not_world_truth_and_still_escalates():
    case = _make_case()
    interp = await apply_reply_signal(
        case,
        "We're disputing this invoice -- the amount billed doesn't match what we agreed to.",
        _FakeLedger(),
        now=datetime(2026, 9, 3),
    )
    assert interp.reply_type == "dispute"
    # world.status must NOT be overwritten with the customer's claim.
    assert case["world"]["status"] == "open"
    # It is recorded as a dialogue-level claim instead.
    assert case["dialogue"]["customer_claimed_disputed"] is True
    # The case still escalates via case state.
    assert case["state"] == "disputed"

    world = WorldSnapshot.from_dict(case["world"])
    dialogue = DialogueSnapshot.from_dict(case["dialogue"])
    objective, _ = choose_objective(
        case["state"],
        world,
        dialogue,
        AutonomyBudget(),
        CONFIDENT,
        active_commitment_type=None,
        contact_target="customer",
    )
    assert objective == "escalate_handoff"

    stack = build_goal_stack(
        case["state"],
        world,
        dialogue,
        AutonomyBudget(),
        CONFIDENT,
        active_commitment_type=None,
        contact_target="customer",
    )
    assert stack.current_objective == "escalate_handoff"

    # A send is still suppressed off case state, even though world.status
    # never flipped to "disputed".
    result = check_before_send(case, "some outbound draft", now=datetime(2026, 9, 3))
    assert result.allowed is False
    assert "disputed" in (result.reason or "")
