"""TemplateCommunicationGenerator is the safe-by-construction fallback used
when the critic blocks an AI draft twice (see executor.py/traced_loop.py's
safe-template fallback). These cover the reflexion_reask improvement
(2026-08-18, user feedback): a missed-promise follow-up to the PM should
name the actual missed date and offer the same either/or choice
pm_awareness_check does, not a vague "what date can we expect payment"."""
from __future__ import annotations

from app.outcome_agent.loop.communication import DEFAULT_GENERATOR


def test_reflexion_reask_to_pm_offers_customer_handoff_choice():
    case = {
        "invoice_no": "INV-1",
        "amount": 42500,
        "target": "pm",
        "commitments": [{"type": "payment_date", "status": "missed", "date": "2026-08-20"}],
    }
    text = DEFAULT_GENERATOR.generate(case, "reflexion_reask", "recover_missed_promise")
    assert "2026-08-20" in text
    assert "new date" in text
    assert "reach out to the customer directly" in text


def test_reflexion_reask_to_customer_has_no_pm_handoff_language():
    case = {
        "invoice_no": "INV-1",
        "amount": 42500,
        "target": "customer",
        "commitments": [{"type": "payment_date", "status": "missed", "date": "2026-08-20"}],
    }
    text = DEFAULT_GENERATOR.generate(case, "reflexion_reask", "recover_missed_promise")
    assert "2026-08-20" in text
    assert "reach out to the customer" not in text


def test_reflexion_reask_without_a_missed_commitment_still_generic_but_valid():
    case = {"invoice_no": "INV-1", "amount": 42500, "target": "pm", "commitments": []}
    text = DEFAULT_GENERATOR.generate(case, "reflexion_reask", "recover_missed_promise")
    assert "INV-1" in text
    assert "reach out to the customer directly" in text
