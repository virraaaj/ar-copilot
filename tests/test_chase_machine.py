"""Tests: chase_machine.py pure transition logic (added 2026-07-20, Phase
C0). No I/O -- every test builds a chase dict by hand and asserts on the
returned Decision. This is the module PLAN_AGENTIC_CHASE.md says to get
exhaustively right, since it's the only thing enforcing the budget/
escalation invariants."""
from __future__ import annotations

from datetime import date, timedelta

from app.services.chase_machine import (
    ChaseConfig,
    Escalate,
    SendMessage,
    escalate_now,
    on_commitment_due,
    on_nudge_check,
    on_reply,
    on_verify_payment_timeout,
    start_pm_outreach,
)
from app.services.chase_parser import ParsedReply

CONFIG = ChaseConfig(max_nudges=3, max_missed_commitments=3, max_commitment_days=90,
                      grace_days=2, payment_verify_days=3, nudge_interval_days=3, max_clarifications=1)


def base_chase(**overrides):
    chase = {
        "id": "chase-1",
        "case_id": "case-1",
        "case_key": "V2-AUTO-1",
        "invoice_no": "INV-1",
        "state": "pending",
        "target": None,
        "pm_email": None,
        "customer_email": None,
        "promised_date": None,
        "promised_by": None,
        "missed_count": 0,
        "nudge_count": 0,
        "clarify_count": 0,
    }
    chase.update(overrides)
    return chase


def future_date(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def past_date(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


# ---- start_pm_outreach ------------------------------------------------


def test_start_pm_outreach_targets_pm_and_sends_message():
    chase = base_chase()
    decision = start_pm_outreach(chase, pm_email="pm@corehelix.ai", config=CONFIG)

    assert decision.updates["state"] == "awaiting_pm"
    assert decision.updates["target"] == "pm"
    assert decision.updates["pm_email"] == "pm@corehelix.ai"
    assert decision.updates["nudge_count"] == 0
    assert len(decision.actions) == 1
    assert isinstance(decision.actions[0], SendMessage)
    assert decision.actions[0].target == "pm"
    assert decision.actions[0].kind == "outreach"


# ---- on_nudge_check -----------------------------------------------------


def test_nudge_check_sends_nudge_when_under_budget():
    chase = base_chase(state="awaiting_pm", target="pm", nudge_count=1)
    decision = on_nudge_check(chase, config=CONFIG)

    assert decision.updates["nudge_count"] == 2
    assert decision.updates.get("state") != "escalated"
    assert isinstance(decision.actions[0], SendMessage)
    assert decision.actions[0].kind == "nudge"


def test_nudge_check_escalates_once_budget_exhausted():
    chase = base_chase(state="awaiting_pm", target="pm", nudge_count=3)
    decision = on_nudge_check(chase, config=CONFIG)

    assert decision.updates["state"] == "escalated"
    assert len(decision.actions) == 1
    assert isinstance(decision.actions[0], Escalate)


def test_nudge_check_targets_customer_when_that_is_current_target():
    chase = base_chase(state="awaiting_customer", target="customer", nudge_count=0, customer_email="cust@x.com")
    decision = on_nudge_check(chase, config=CONFIG)

    assert decision.actions[0].target == "customer"


# ---- on_reply: commitment_date ------------------------------------------


def test_reply_high_confidence_commitment_tracks_it():
    chase = base_chase(state="awaiting_pm", target="pm")
    parsed = ParsedReply(intent="commitment_date", confidence="high", promised_date=future_date(10))

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates["state"] == "commitment_tracked"
    assert decision.updates["promised_date"] == parsed.promised_date
    assert decision.updates["promised_by"] == "pm"
    assert decision.updates["nudge_count"] == 0
    assert decision.updates["clarify_count"] == 0
    assert decision.actions[0].kind == "confirm"


def test_reply_commitment_in_the_past_is_treated_as_unclear_and_clarifies():
    chase = base_chase(state="awaiting_pm", target="pm", clarify_count=0)
    parsed = ParsedReply(intent="commitment_date", confidence="high", promised_date=past_date(5))

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") != "commitment_tracked"
    assert decision.actions[0].kind == "clarify"


def test_reply_commitment_too_far_out_escalates():
    chase = base_chase(state="awaiting_pm", target="pm")
    parsed = ParsedReply(intent="commitment_date", confidence="high", promised_date=future_date(200))

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates["state"] == "escalated"
    assert isinstance(decision.actions[0], Escalate)


# ---- on_reply: handoff_to_customer ---------------------------------------


def test_reply_handoff_with_email_switches_target_to_customer():
    chase = base_chase(state="awaiting_pm", target="pm")
    parsed = ParsedReply(intent="handoff_to_customer", confidence="high", customer_contact_email="cust@x.com")

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates["state"] == "awaiting_customer"
    assert decision.updates["target"] == "customer"
    assert decision.updates["customer_email"] == "cust@x.com"
    assert decision.updates["nudge_count"] == 0
    assert decision.actions[0].target == "customer"
    assert decision.actions[0].kind == "outreach"


def test_reply_handoff_uses_existing_customer_email_if_reply_omits_it():
    chase = base_chase(state="awaiting_pm", target="pm", customer_email="onfile@x.com")
    parsed = ParsedReply(intent="handoff_to_customer", confidence="high", customer_contact_email=None)

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates["state"] == "awaiting_customer"
    assert decision.actions[0].target == "customer"


def test_reply_handoff_with_no_email_anywhere_asks_pm_for_it():
    chase = base_chase(state="awaiting_pm", target="pm", customer_email=None)
    parsed = ParsedReply(intent="handoff_to_customer", confidence="high", customer_contact_email=None)

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") is None or decision.updates.get("state") == chase["state"]
    assert decision.actions[0].target == "pm"
    assert decision.actions[0].kind == "ask_for_customer_email"
    assert decision.updates["clarify_count"] == 1


def test_reply_handoff_from_customer_itself_is_treated_as_unclear():
    chase = base_chase(state="awaiting_customer", target="customer")
    parsed = ParsedReply(intent="handoff_to_customer", confidence="high", customer_contact_email="someone@x.com")

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") != "awaiting_customer" or decision.actions[0].kind == "clarify"
    assert decision.actions[0].kind == "clarify"


# ---- on_reply: handoff_to_contact (added 2026-07-23) ----------------------


def test_reply_handoff_to_contact_routes_to_resolved_role_email():
    chase = base_chase(state="awaiting_pm", target="pm")
    parsed = ParsedReply(intent="handoff_to_contact", confidence="high", contact_role="bu_finance")

    decision = on_reply(chase, parsed, config=CONFIG, resolved_contact_email="finance@x.com")

    assert decision.updates["state"] == "awaiting_contact"
    assert decision.updates["target"] == "bu_finance"
    assert decision.updates["contact_email"] == "finance@x.com"
    assert decision.updates["nudge_count"] == 0
    assert decision.updates["clarify_count"] == 0
    assert decision.actions[0].target == "bu_finance"
    assert decision.actions[0].kind == "outreach"
    assert decision.events[0][0] == "handoff_to_contact"


def test_reply_handoff_to_contact_with_no_resolved_email_clarifies_instead():
    """The engine (chase_engine.py, not this pure module) looks the role up
    against the project's contacts before calling on_reply -- if nothing
    was found, resolved_contact_email is None and this must not silently
    invent a target with nowhere to actually send."""
    chase = base_chase(state="awaiting_pm", target="pm")
    parsed = ParsedReply(intent="handoff_to_contact", confidence="high", contact_role="legal")

    decision = on_reply(chase, parsed, config=CONFIG, resolved_contact_email=None)

    assert decision.updates.get("state") != "awaiting_contact"
    assert decision.actions[0].kind == "clarify"


def test_reply_handoff_to_contact_low_confidence_falls_through_to_clarify():
    chase = base_chase(state="awaiting_pm", target="pm")
    parsed = ParsedReply(intent="handoff_to_contact", confidence="low", contact_role="bu_finance")

    decision = on_reply(chase, parsed, config=CONFIG, resolved_contact_email="finance@x.com")

    assert decision.updates.get("state") != "awaiting_contact"
    assert decision.actions[0].kind == "clarify"


def test_missed_commitment_rechase_to_a_contact_role_uses_generic_awaiting_state():
    """A commitment promised by a non-pm/non-customer contact (e.g. BU
    Finance) that's later missed must rechase into 'awaiting_contact', not
    an unbounded 'awaiting_bu_finance' -- ChaseStore.OPEN_STATES is a fixed
    set, not one entry per possible project-contact role."""
    chase = base_chase(
        state="commitment_tracked", target="bu_finance", promised_by="bu_finance",
        promised_date=past_date(3), contact_email="finance@x.com",
    )

    decision = on_commitment_due(chase, paid=False, config=CONFIG)

    assert decision.updates["state"] == "awaiting_contact"
    assert decision.actions[0].target == "bu_finance"


# ---- on_reply: claims_paid / dispute -------------------------------------


def test_reply_claims_paid_moves_to_verifying_payment():
    chase = base_chase(state="awaiting_customer", target="customer")
    parsed = ParsedReply(intent="claims_paid", confidence="high")

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates["state"] == "verifying_payment"
    assert decision.actions[0].kind == "verify_check"


def test_reply_dispute_escalates_immediately():
    chase = base_chase(state="awaiting_pm", target="pm")
    parsed = ParsedReply(intent="dispute", confidence="high")

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates["state"] == "escalated"
    assert isinstance(decision.actions[0], Escalate)


def test_reply_low_confidence_dispute_clarifies_instead_of_escalating():
    """Regression test (added 2026-07-24): a customer said 'let me check
    with the team, I heard service wasn't up to par' -- investigating a
    possible concern, not a confident refusal to pay -- and it escalated
    to a human immediately anyway, with zero chance for the customer to
    come back with a real commitment. dispute must only bypass the
    clarify budget when the model is actually confident it's a genuine
    dispute; a low-confidence/tentative one gets a clarifying question
    first, same as any other ambiguous reply."""
    chase = base_chase(state="awaiting_customer", target="customer", clarify_count=0)
    parsed = ParsedReply(intent="dispute", confidence="low")

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") != "escalated"
    assert decision.actions[0].kind == "clarify"
    assert not any(isinstance(a, Escalate) for a in decision.actions)


# ---- on_reply: checkback_requested ---------------------------------------


def test_reply_checkback_with_a_date_schedules_followup_and_does_not_escalate():
    chase = base_chase(state="awaiting_pm", target="pm", postpone_count=0)
    parsed = ParsedReply(intent="checkback_requested", confidence="high", followup_date=future_date(5))

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") != "escalated"
    assert decision.updates["postpone_count"] == 1
    assert decision.updates["next_action_at"].startswith(future_date(5))
    assert decision.actions[0].kind == "checkback_ack"
    assert decision.actions[0].target == "pm"
    assert not any(isinstance(a, Escalate) for a in decision.actions)


def test_reply_checkback_with_no_date_asks_when_to_follow_up():
    """"Let me check on this" with no timeframe -- per the boss's guidance,
    ask when a good time to follow up is instead of immediately treating it
    like any other no-commitment stall."""
    chase = base_chase(state="awaiting_customer", target="customer", postpone_count=0)
    parsed = ParsedReply(intent="checkback_requested", confidence="high", followup_date=None)

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") != "escalated"
    assert decision.updates["postpone_count"] == 1
    assert "follow up" in decision.actions[0].text.lower()
    assert decision.actions[0].kind == "checkback_ack"


def test_reply_checkback_past_followup_date_falls_back_to_normal_interval():
    chase = base_chase(state="awaiting_pm", target="pm", postpone_count=0)
    parsed = ParsedReply(intent="checkback_requested", confidence="high", followup_date=past_date(2))

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") != "escalated"
    assert not decision.updates["next_action_at"].startswith(past_date(2))


def test_reply_checkback_escalates_after_repeated_postponing_with_no_progress():
    """Guardrail: postponing itself is fine, but only up to a budget --
    keep postponing without ever committing to a payment date or paying,
    and it escalates to a human, same shape as the nudge/clarify/missed-
    commitment budgets."""
    chase = base_chase(state="awaiting_pm", target="pm", postpone_count=3)
    parsed = ParsedReply(intent="checkback_requested", confidence="high", followup_date=future_date(5))

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates["state"] == "escalated"
    assert isinstance(decision.actions[0], Escalate)
    assert "postpon" in decision.actions[0].reason.lower()


# ---- on_reply: out_of_scope_request ---------------------------------------


def test_reply_out_of_scope_declines_and_redirects_to_the_last_question():
    """Regression test (added 2026-07-24): a customer who'd just been asked
    'when would be a good time to follow up on this?' (a checkback ack, not
    a payment-date question) instead asked about other customers -- and got
    sent the generic 'is there a specific date I should expect payment by?'
    even though that question had never been asked and wasn't the open
    one. The redirect must repeat the actual last question asked."""
    chase = base_chase(state="awaiting_customer", target="customer", clarify_count=0)
    parsed = ParsedReply(intent="out_of_scope_request", confidence="high")

    decision = on_reply(
        chase, parsed, config=CONFIG, last_question="No worries -- when would be a good time for me to follow up on this?"
    )

    assert decision.updates.get("state") != "escalated"
    text = decision.actions[0].text.lower()
    assert "other customers" in text or "other invoices" in text
    assert "when would be a good time" in text
    assert "payment by" not in text


def test_reply_out_of_scope_falls_back_to_payment_question_with_no_history():
    chase = base_chase(state="awaiting_customer", target="customer", clarify_count=0)
    parsed = ParsedReply(intent="out_of_scope_request", confidence="high")

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") != "escalated"
    assert "payment by" in decision.actions[0].text.lower()


def test_reply_out_of_scope_escalates_after_clarify_budget_exhausted():
    chase = base_chase(state="awaiting_customer", target="customer", clarify_count=1)
    parsed = ParsedReply(intent="out_of_scope_request", confidence="high")

    decision = on_reply(chase, parsed, config=CONFIG, last_question="Is there a payment date you can share?")

    assert decision.updates["state"] == "escalated"
    assert isinstance(decision.actions[0], Escalate)


def test_reply_low_confidence_checkback_falls_through_to_clarify():
    chase = base_chase(state="awaiting_pm", target="pm")
    parsed = ParsedReply(intent="checkback_requested", confidence="low")

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") != "escalated"
    assert decision.actions[0].kind == "clarify"


# ---- on_reply: no_commitment / unclear / low confidence ------------------


def test_reply_no_commitment_asks_one_clarifying_question():
    chase = base_chase(state="awaiting_pm", target="pm", clarify_count=0)
    parsed = ParsedReply(intent="no_commitment", confidence="low")

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates["clarify_count"] == 1
    assert decision.actions[0].kind == "clarify"


def test_reply_still_unclear_after_clarify_budget_escalates():
    chase = base_chase(state="awaiting_pm", target="pm", clarify_count=1)
    parsed = ParsedReply(intent="unclear", confidence="low")

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates["state"] == "escalated"
    assert isinstance(decision.actions[0], Escalate)


def test_low_confidence_commitment_is_treated_as_unclear_not_tracked():
    chase = base_chase(state="awaiting_pm", target="pm", clarify_count=0)
    parsed = ParsedReply(intent="commitment_date", confidence="low", promised_date=future_date(5))

    decision = on_reply(chase, parsed, config=CONFIG)

    assert decision.updates.get("state") != "commitment_tracked"
    assert decision.actions[0].kind == "clarify"


# ---- on_commitment_due ----------------------------------------------------


def test_commitment_due_paid_closes_chase():
    chase = base_chase(state="commitment_tracked", promised_by="pm", promised_date=past_date(1))

    decision = on_commitment_due(chase, paid=True, config=CONFIG)

    assert decision.updates["state"] == "closed_paid"
    assert decision.actions == []


def test_commitment_due_unpaid_rechases_whoever_promised():
    chase = base_chase(state="commitment_tracked", promised_by="customer", target="customer",
                        promised_date=past_date(1), missed_count=0)

    decision = on_commitment_due(chase, paid=False, config=CONFIG)

    assert decision.updates["state"] == "awaiting_customer"
    assert decision.updates["missed_count"] == 1
    assert decision.updates["promised_date"] is None
    assert decision.actions[0].target == "customer"
    assert decision.actions[0].kind == "rechase"


def test_commitment_due_unpaid_escalates_once_miss_budget_exhausted():
    chase = base_chase(state="commitment_tracked", promised_by="pm", target="pm",
                        promised_date=past_date(1), missed_count=2)

    decision = on_commitment_due(chase, paid=False, config=CONFIG)

    assert decision.updates["state"] == "escalated"
    assert decision.updates["missed_count"] == 3
    assert isinstance(decision.actions[0], Escalate)


# ---- on_verify_payment_timeout --------------------------------------------


def test_verify_payment_timeout_paid_closes_chase():
    chase = base_chase(state="verifying_payment", target="pm")

    decision = on_verify_payment_timeout(chase, paid=True, config=CONFIG)

    assert decision.updates["state"] == "closed_paid"


def test_verify_payment_timeout_unpaid_rechases_and_counts_as_a_miss():
    chase = base_chase(state="verifying_payment", target="customer", missed_count=0)

    decision = on_verify_payment_timeout(chase, paid=False, config=CONFIG)

    assert decision.updates["state"] == "awaiting_customer"
    assert decision.updates["missed_count"] == 1
    assert decision.actions[0].kind == "verify_check"


def test_verify_payment_timeout_unpaid_escalates_at_budget():
    chase = base_chase(state="verifying_payment", target="pm", missed_count=2)

    decision = on_verify_payment_timeout(chase, paid=False, config=CONFIG)

    assert decision.updates["state"] == "escalated"
    assert isinstance(decision.actions[0], Escalate)


# ---- idempotency / budget sanity ------------------------------------------


def test_nudge_budget_is_never_exceeded_across_repeated_calls():
    chase = base_chase(state="awaiting_pm", target="pm", nudge_count=0)
    for _ in range(CONFIG.max_nudges):
        decision = on_nudge_check(chase, config=CONFIG)
        chase.update(decision.updates)
        assert chase["state"] != "escalated"

    final_decision = on_nudge_check(chase, config=CONFIG)
    assert final_decision.updates["state"] == "escalated"


# ---- escalate_now (smart escalation judgment, added 2026-07-22) -----------


def test_escalate_now_sets_state_and_clears_next_action():
    chase = base_chase(state="awaiting_pm", target="pm", nudge_count=0)

    decision = escalate_now(chase, reason="AI judged the conversation as concerning")

    assert decision.updates["state"] == "escalated"
    assert decision.updates["next_action_at"] is None
    assert isinstance(decision.actions[0], Escalate)
    assert decision.actions[0].reason == "AI judged the conversation as concerning"


def test_escalate_now_works_regardless_of_current_budget_state():
    # Even a chase with zero nudges/misses used -- proves this bypasses
    # the counters entirely rather than checking them.
    chase = base_chase(state="awaiting_customer", target="customer", nudge_count=0, missed_count=0)

    decision = escalate_now(chase, reason="hostile reply")

    assert decision.updates["state"] == "escalated"
