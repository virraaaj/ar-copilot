"""Invariant tests for FIX_PLAN_commitment_grounding.md (2026-08-20).

Scenario tests (test_guided_demo.py, test_goals.py's tie-break tests, etc.)
verify a specific transcript. These verify the *class* of bug that kept
recurring -- commitment TYPE getting erased before routing (RC1), no
grounding check on factual claims in a draft (RC2), and the LLM
underperforming the deterministic dispute regex (RC3) -- so a future change
that reintroduces any of these has to break a test named for the invariant
it's breaking, not just get lucky that no scenario transcript happened to
exercise it.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.outcome_agent.adapters.llm_tools import ScriptedLLM, interpret_reply_llm
from app.outcome_agent.domain.dialogue_model import DialogueSnapshot
from app.outcome_agent.domain.goals import build_goal_stack, choose_objective
from app.outcome_agent.domain.types import AutonomyBudget, Uncertainty
from app.outcome_agent.domain.world_model import WorldSnapshot
from app.outcome_agent.loop.critic import critique

CONFIDENT = Uncertainty(confidence=1.0, needs_clarification=False, unclear_fields=[], note="")

_OPEN_WORLD = WorldSnapshot(
    invoice_no="INV-1", case_id="c1", balance_due=1000.0, status="open", due_date="2026-07-15"
)
_STATES = [
    "overdue", "due", "outreach_ready", "waiting_for_customer",
    "follow_up_scheduled", "promise_to_pay", "blocked", "promise_missed",
]
_COMMITMENT_TYPES = [None, "payment_date", "follow_up_date", "blocker_resolution_date"]


# --- Invariant 1: confirm_promise is never selected without an active ------
# --- payment_date commitment (property test across states x types) --------

@pytest.mark.parametrize("state", _STATES)
@pytest.mark.parametrize("commitment_type", _COMMITMENT_TYPES)
def test_confirm_promise_never_selected_without_payment_date_commitment(state, commitment_type):
    dialogue = DialogueSnapshot(latest_inbound="some prior reply")
    goals = build_goal_stack(
        state, _OPEN_WORLD, dialogue, AutonomyBudget(), CONFIDENT,
        active_commitment_type=commitment_type, contact_target="customer",
    )
    if goals.selected_tactic == "confirm_promise":
        assert commitment_type == "payment_date"


# --- Invariant 2: no draft asserts a payment date absent from active -------
# --- commitments -- assert no_fabricated_commitment fires on the exact -----
# --- Silverline draft text --------------------------------------------------

def test_no_fabricated_commitment_fires_on_the_silverline_draft():
    # The actual fabricated sentence from the live bug: a checkback
    # (follow_up_date) commitment was due, routing sent the LLM to
    # confirm_promise, and it invented a payment date from the invoice's
    # due_date instead of acknowledging an actual payment promise.
    draft = (
        "Thanks for the update -- I've noted that Silverline Manufacturing "
        "is expected to pay by July 15th, and I'll follow up if anything "
        "changes."
    )
    context = {
        "invoice_no": "DEMO-INV-1002",
        "world": {},
        "goal_stack": {},
        "failed_asks": [],
        # No active payment_date commitment on record -- only the
        # follow_up_date checkback that was actually made.
        "active_commitments": [
            {"type": "follow_up_date", "date": "2026-08-29", "status": "active"}
        ],
    }
    result = critique(draft, context, {"tactic": "confirm_promise", "objective": "obtain_commitment", "score": 1})
    assert not result.passed
    assert any(c["name"] == "no_fabricated_commitment" and not c["pass"] for c in result.checks)


def test_no_fabricated_commitment_passes_when_the_date_matches_a_real_commitment():
    draft = "Thanks for the update -- I've noted that Acme Co is expected to pay by August 20th."
    context = {
        "invoice_no": "INV-1",
        "world": {},
        "goal_stack": {},
        "failed_asks": [],
        "active_commitments": [
            {"type": "payment_date", "date": "2026-08-20", "status": "active"}
        ],
    }
    result = critique(draft, context, {"tactic": "confirm_promise", "objective": "obtain_commitment", "score": 1})
    assert any(c["name"] == "no_fabricated_commitment" and c["pass"] for c in result.checks)


# --- Invariant 3: explicit dispute language always routes to ---------------
# --- escalate_handoff, and the LLM label cannot override it ----------------

@pytest.mark.parametrize(
    "text",
    [
        "Actually, we're disputing this invoice -- the amount billed doesn't match what we agreed to.",
        "We dispute this charge entirely.",
        "The quantities are wrong on this invoice, we do not owe this amount.",
    ],
)
@pytest.mark.asyncio
async def test_dispute_language_overrides_a_conflicting_llm_label(text):
    # The LLM mislabels every one of these as approval_blocker (mirroring
    # the live bug: the Silverline dispute was classified `blocker` at 90%
    # confidence) -- the deterministic dispute marker must win anyway.
    llm = ScriptedLLM(
        [
            {
                "name": "record_reply_interpretation",
                "arguments": {
                    "reply_type": "approval_blocker",
                    "confidence": 0.9,
                    "summary": "customer needs to check something before paying",
                },
            }
        ]
    )
    interp, _ = await interpret_reply_llm(
        llm, text, {"case": {}, "world": {}}, mock=False, now=datetime(2026, 8, 20)
    )
    assert interp.reply_type == "dispute"


def test_disputed_state_always_escalates():
    dialogue = DialogueSnapshot(latest_inbound="we dispute this invoice")
    objective, _ = choose_objective(
        "disputed", _OPEN_WORLD, dialogue, AutonomyBudget(), CONFIDENT,
        active_commitment_type=None, contact_target="customer",
    )
    assert objective == "escalate_handoff"


# --- Invariant 4: a follow_up_date commitment coming due never routes to ---
# --- obtain_commitment -------------------------------------------------------

def test_followup_date_due_never_routes_to_obtain_commitment():
    dialogue = DialogueSnapshot(latest_inbound="I'll check on this and get back to you")
    goals = build_goal_stack(
        "follow_up_scheduled", _OPEN_WORLD, dialogue, AutonomyBudget(), CONFIDENT,
        active_commitment_type="follow_up_date", contact_target="customer",
    )
    assert goals.current_objective != "obtain_commitment"
    assert goals.selected_tactic != "confirm_promise"
