"""resolve_recipient() is the single safety boundary between "message the
PM" and "message the customer" -- these are regression tests for the
2026-08-18 fix: the old version's fallback chains crossed between
pm_email and customer_email (and both ended in a literal placeholder
address), so a PM-directed message could leak to the customer's inbox, or
vice versa, whenever the "preferred" address was empty. It must now
return None in that case rather than guess."""
from __future__ import annotations

from app.outcome_agent.loop.communication import resolve_recipient


def test_pm_target_returns_pm_email():
    case = {"target": "pm", "pm_email": "pm@corehelix.ai", "customer_email": "ap@customer.example"}
    assert resolve_recipient(case, "firm_reminder") == "pm@corehelix.ai"


def test_pm_target_falls_back_to_internal_owner_not_customer_email():
    case = {"target": "pm", "pm_email": None, "customer_email": "ap@customer.example", "world": {"internal_owner": "owner@corehelix.ai"}}
    assert resolve_recipient(case, "firm_reminder") == "owner@corehelix.ai"


def test_pm_target_with_no_pm_address_returns_none_not_customer_email():
    # The actual bug: this used to fall back to customer_email, sending a
    # PM-directed "do you know a payment date" check straight to the
    # customer's inbox.
    case = {"target": "pm", "pm_email": None, "customer_email": "ap@customer.example", "world": {}}
    assert resolve_recipient(case, "firm_reminder") is None


def test_customer_target_returns_customer_email():
    case = {"target": "customer", "pm_email": "pm@corehelix.ai", "customer_email": "ap@customer.example"}
    assert resolve_recipient(case, "firm_reminder") == "ap@customer.example"


def test_customer_target_with_no_customer_address_returns_none_not_pm_email():
    # The mirror bug: this used to fall back to pm_email, sending a
    # customer-facing message to the PM's own inbox.
    case = {"target": "customer", "pm_email": "pm@corehelix.ai", "customer_email": None}
    assert resolve_recipient(case, "firm_reminder") is None


def test_pm_directed_tactic_wins_even_if_target_is_customer():
    # pm_awareness_check must always reach the PM, regardless of target --
    # it's the tactic that asks the PM before any customer contact exists.
    case = {"target": "customer", "pm_email": "pm@corehelix.ai", "customer_email": "ap@customer.example"}
    assert resolve_recipient(case, "pm_awareness_check") == "pm@corehelix.ai"


def test_never_returns_a_placeholder_address():
    case = {"target": "customer", "pm_email": None, "customer_email": None}
    assert resolve_recipient(case, "firm_reminder") is None
