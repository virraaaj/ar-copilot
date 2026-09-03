"""Tests: chase_guardrails.py (added 2026-07-28, spec §6.15)."""
from __future__ import annotations

from datetime import date

from app.services.chase_guardrails import (
    BLACKOUT_DATES,
    check_blackout_date,
    check_message_language,
)


def test_blackout_date_allows_normal_days():
    result = check_blackout_date(date(2026, 7, 28))
    assert result.allowed is True


def test_blackout_date_blocks_configured_date(monkeypatch):
    monkeypatch.setattr("app.services.chase_guardrails.BLACKOUT_DATES", ["2026-12-25"])
    result = check_blackout_date(date(2026, 12, 25))
    assert result.allowed is False
    assert "blackout" in result.reason.lower()


def test_message_language_allows_normal_text():
    result = check_message_language("Following up on invoice INV-1, could you confirm a payment date?")
    assert result.allowed is True


def test_message_language_blocks_discount_offer():
    result = check_message_language("We could offer a 10% discount if you pay this week.")
    assert result.allowed is False
    assert "discount" in result.reason.lower()


def test_message_language_blocks_legal_threat():
    result = check_message_language("If this isn't paid we will pursue legal action.")
    assert result.allowed is False
    assert "legal action" in result.reason.lower()


def test_message_language_blocks_collections_agency_mention():
    result = check_message_language("This will be sent to a collections agency.")
    assert result.allowed is False


# -- Rule A: no authority to change the terms of the debt -------------------

def test_message_language_blocks_extension_offer():
    result = check_message_language("We can offer you an extension if needed.")
    assert result.allowed is False


def test_message_language_blocks_extend_due_date():
    result = check_message_language("We could extend the due date to the end of the month.")
    assert result.allowed is False


def test_message_language_blocks_extend_payment_deadline():
    result = check_message_language("I can extend your payment deadline.")
    assert result.allowed is False


def test_message_language_blocks_would_an_extension_help():
    result = check_message_language("Would an extension help in this case?")
    assert result.allowed is False


def test_message_language_allows_extended_project_scope():
    result = check_message_language("Thanks for confirming the extended project scope was approved.")
    assert result.allowed is True


def test_message_language_allows_extended_warranty_period():
    result = check_message_language("This covers the extended warranty period.")
    assert result.allowed is True


def test_message_language_allows_extended_team():
    result = check_message_language("The extended team reviewed it.")
    assert result.allowed is True


def test_message_language_blocks_more_time():
    result = check_message_language("Let us know if you need more time to pay.")
    assert result.allowed is False


def test_message_language_blocks_payment_plan():
    result = check_message_language("We could set up a payment plan for the balance.")
    assert result.allowed is False


def test_message_language_blocks_instalment():
    result = check_message_language("You could pay this off in instalments.")
    assert result.allowed is False


def test_message_language_blocks_installment_spelling():
    result = check_message_language("You could pay this off in installments.")
    assert result.allowed is False


def test_message_language_blocks_revised_due_date():
    result = check_message_language("We can give you a revised due date for this invoice.")
    assert result.allowed is False


def test_message_language_blocks_credit_terms_change():
    result = check_message_language("We're happy to adjust the terms on this account.")
    assert result.allowed is False


def test_message_language_blocks_revise_the_terms():
    result = check_message_language("We can revise the terms for you.")
    assert result.allowed is False


def test_message_language_allows_factual_payment_terms_mention():
    result = check_message_language("Our records show the payment terms are net-30.")
    assert result.allowed is True


def test_message_language_allows_factual_agreement_terms_mention():
    result = check_message_language("The terms of the agreement state net-30.")
    assert result.allowed is True


def test_message_language_blocks_negotiation_invite():
    result = check_message_language("Let us know what works for you and we'll go from there.")
    assert result.allowed is False


# -- Rule B: never state or imply a consequence of non-payment --------------

def test_message_language_blocks_suspend_service():
    result = check_message_language("If this remains unpaid, we may suspend the service.")
    assert result.allowed is False


def test_message_language_blocks_withhold_work():
    result = check_message_language("We will need to withhold the work until this is settled.")
    assert result.allowed is False


def test_message_language_blocks_account_hold():
    result = check_message_language("Please be aware your account is on hold.")
    assert result.allowed is False


# -- Legitimate messages that must keep passing ------------------------------

def test_message_language_allows_request_for_expected_payment_date():
    result = check_message_language(
        "Could you give us an update on where things stand, including an expected payment date?"
    )
    assert result.allowed is True


def test_message_language_allows_payment_commitment_recorded():
    result = check_message_language("Thanks -- I've recorded your payment commitment for INV-123.")
    assert result.allowed is True


def test_message_language_allows_reminder_confirm_payment_date():
    result = check_message_language(
        "Reminder: INV-123 for $45,000 remains open. Please confirm a payment date."
    )
    assert result.allowed is True


def test_message_language_allows_ask_about_expected_payment_date():
    result = check_message_language(
        "Are you already aware of an expected payment date for this invoice, "
        "or should we go ahead and reach out to the customer directly?"
    )
    assert result.allowed is True


def test_message_language_allows_new_date_question():
    result = check_message_language(
        "The August 20th date didn't come through -- is there a new date we should track?"
    )
    assert result.allowed is True


# -- Rule C: partial payment is never accepted on an overdue invoice --------
# Added 2026-09-03. Four sentences below are the exact red-team leaks; the
# rest cover the same partial-payment concept in other phrasings.

def test_message_language_blocks_pay_half_now_leak():
    result = check_message_language("If you can pay half now, we can look at the rest later.")
    assert result.allowed is False


def test_message_language_blocks_break_into_smaller_amounts_leak():
    result = check_message_language("We could break this into smaller amounts over the next few months.")
    assert result.allowed is False


def test_message_language_blocks_part_payment_leak():
    result = check_message_language("Would a part payment help in the meantime?")
    assert result.allowed is False


def test_message_language_blocks_any_amount_you_can_leak():
    result = check_message_language("Any amount you can send now would help.")
    assert result.allowed is False


def test_message_language_blocks_partial_payment():
    result = check_message_language("We would accept a partial payment on this invoice.")
    assert result.allowed is False


def test_message_language_blocks_split_the_balance():
    result = check_message_language("Could we split the balance across two payments?")
    assert result.allowed is False


def test_message_language_blocks_split_this_into():
    result = check_message_language("We could split this into two smaller payments.")
    assert result.allowed is False


def test_message_language_blocks_something_on_account():
    result = check_message_language("Could you send something on account for now?")
    assert result.allowed is False


def test_message_language_blocks_pay_what_you_can():
    result = check_message_language("Please just pay what you can for now.")
    assert result.allowed is False


def test_message_language_allows_full_amount_outstanding():
    result = check_message_language("The full amount remains outstanding.")
    assert result.allowed is True


def test_message_language_allows_invoice_amount_statement():
    result = check_message_language("The invoice amount is $45,000.")
    assert result.allowed is True


def test_message_language_allows_confirm_payment_amount():
    result = check_message_language("Please confirm the payment amount you have processed.")
    assert result.allowed is True


def test_message_language_allows_payment_commitment_recorded_full():
    result = check_message_language("Thanks -- I have recorded your payment commitment for INV-7104.")
    assert result.allowed is True


def test_message_language_allows_firm_reminder_template_text():
    result = check_message_language("Reminder: INV-7104 for $45,000 remains open. Please confirm a payment date.")
    assert result.allowed is True


def test_message_language_allows_polite_outreach_template_text():
    result = check_message_language(
        "Could you give us an update on where things stand, including an expected payment date?"
    )
    assert result.allowed is True


def test_message_language_allows_reflexion_reask_template_text():
    result = check_message_language(
        "The August 20th date didn't come through -- is there a new date we should track?"
    )
    assert result.allowed is True


def test_message_language_allows_terms_statement():
    result = check_message_language("Our records show the payment terms are net-30.")
    assert result.allowed is True


def test_all_template_generator_outputs_pass_guardrail():
    """All 12 outputs of TemplateCommunicationGenerator (11 tactics + the
    force_threat harness draft) must be allowed EXCEPT force_threat, which
    is intentionally a bad draft used only by the S7 harness to prove the
    guardrail catches it."""
    from app.outcome_agent.loop.communication import TemplateCommunicationGenerator

    gen = TemplateCommunicationGenerator()
    tactics = [
        "pm_awareness_check", "polite_outreach", "soft_nudge", "firm_reminder",
        "clarify_ask", "confirm_promise", "blocker_ack", "verify_payment_ask",
        "reflexion_reask", "escalation_pack", "dispute_route", "force_threat",
    ]
    case = {
        "invoice_no": "INV-7104", "amount": 45000, "customer_name": "Acme",
        "commitments": [{"type": "payment_date", "status": "missed", "date": "2026-08-20"}],
    }
    for tactic in tactics:
        text = gen.generate(case, tactic, objective="test")
        result = check_message_language(text)
        if tactic == "force_threat":
            assert result.allowed is False, f"{tactic!r} should be blocked: {text!r}"
        else:
            assert result.allowed is True, f"{tactic!r} was wrongly blocked ({result.reason}): {text!r}"
