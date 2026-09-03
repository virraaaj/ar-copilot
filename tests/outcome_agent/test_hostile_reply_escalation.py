"""Regression coverage for the hostile-reply-triggers-another-chase-email bug
(2026-09-03).

Verified chain that produced the bug: reply_interpreter.py classifies replies
like "this is ridiculous, we'll sue you" as ReplyType.HOSTILE at confidence
0.8. signal_ingestion.py had no branch for it, so it fell through to the
generic `else` and set state="customer_responded" -- an ordinary "customer is
engaged" state. goals.choose_objective then had no hostile branch either, and
confidence 0.8 is high enough to clear the <0.55 clarify_date safety-net, so
it fell all the way to the default `establish_contact` objective, whose
tactics are polite_outreach/soft_nudge/firm_reminder -- all customer-facing
chase emails. Net effect: hostility from a customer produced another chase
email instead of a human handoff.

The fix mirrors how "dispute" already escalates: signal_ingestion now routes
ReplyType.HOSTILE through state_machine.transition(state, "hostile"), which
hard-rules to CaseState.ESCALATION_REQUIRED, the same state
choose_objective already treats as "hand off to a human" (goals.py, the
branch shared with budget exhaustion).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.outcome_agent.domain.dialogue_model import DialogueSnapshot
from app.outcome_agent.domain.goals import build_goal_stack, choose_objective
from app.outcome_agent.domain.types import AutonomyBudget, Uncertainty
from app.outcome_agent.domain.world_model import WorldSnapshot
from app.outcome_agent.loop.signal_ingestion import apply_reply_signal

CONFIDENT = Uncertainty(confidence=1.0, needs_clarification=False, unclear_fields=[], note="")

CHASE_TACTICS = {"polite_outreach", "soft_nudge", "firm_reminder"}


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


@pytest.mark.asyncio
async def test_hostile_reply_sets_escalation_required_state():
    case = _make_case()
    interp = await apply_reply_signal(
        case, "This is ridiculous, we will sue you.", _FakeLedger(), now=datetime(2026, 9, 3)
    )
    assert interp.reply_type == "hostile"
    assert case["state"] == "escalation_required"
    # Hostility is a dialogue fact, not a world-truth fact (world_model.py:1)
    # -- must not be conflated with e.g. "disputed".
    assert case["world"]["status"] == "open"


def test_hostile_state_routes_to_escalate_handoff_objective():
    world = WorldSnapshot.from_dict(
        {"invoice_no": "INV-1", "case_id": "case-1", "balance_due": 1000.0, "status": "open"}
    )
    dialogue = DialogueSnapshot(latest_inbound="This is ridiculous, we will sue you.")
    objective, rationale = choose_objective(
        "escalation_required",
        world,
        dialogue,
        AutonomyBudget(),
        CONFIDENT,
        active_commitment_type=None,
        contact_target="customer",
    )
    assert objective == "escalate_handoff"
    assert objective != "establish_contact"


def test_hostile_state_never_selects_a_customer_facing_chase_tactic():
    world = WorldSnapshot.from_dict(
        {"invoice_no": "INV-1", "case_id": "case-1", "balance_due": 1000.0, "status": "open"}
    )
    dialogue = DialogueSnapshot(latest_inbound="This is ridiculous, we will sue you.")
    stack = build_goal_stack(
        "escalation_required",
        world,
        dialogue,
        AutonomyBudget(),
        CONFIDENT,
        active_commitment_type=None,
        contact_target="customer",
    )
    assert stack.current_objective == "escalate_handoff"
    assert stack.selected_tactic == "escalation_pack"
    assert stack.selected_tactic not in CHASE_TACTICS
