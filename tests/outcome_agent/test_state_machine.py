from app.outcome_agent.domain.state_machine import collections_allowed, transition


def test_paid_hard_rule():
    assert transition("waiting_for_customer", "paid") == "paid"


def test_dispute_exits_collections():
    assert transition("customer_responded", "dispute") == "disputed"
    assert not collections_allowed("disputed")


def test_promise_miss_then_outreach():
    assert transition("promise_to_pay", "missed") == "promise_missed"
    assert transition("promise_missed", "outreach") == "waiting_for_customer"
