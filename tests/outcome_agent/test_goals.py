"""choose_tactic() had no test coverage at all before this file. These
lock in the 2026-08-18 fix (found via user review of the guided demo):
with no learned tactic weight yet -- the normal case for a fresh case, or
any tie -- the old code sorted plain (score, tactic_name) tuples, so ties
were broken by comparing the tactic string alphabetically rather than by
each objective's own declared candidate priority. "firm_reminder" beats
"confirm_promise" alphabetically, so confirm_promise was structurally
unreachable on a tie: the agent kept selecting a generic reminder (and,
per its content, re-asking "are you already aware of a date") immediately
after the PM had just given one, instead of acknowledging it."""
from __future__ import annotations

from app.outcome_agent.domain.goals import choose_tactic
from app.outcome_agent.domain.types import Uncertainty

CONFIDENT = Uncertainty(confidence=1.0, needs_clarification=False, unclear_fields=[], note="")


def test_obtain_commitment_prefers_confirm_promise_on_a_weight_tie():
    # active_commitment_type="payment_date" is required here since 2026-08-20
    # (FIX_PLAN_commitment_grounding.md, Fix 1): confirm_promise is now
    # structurally unreachable without an active payment_date commitment --
    # obtain_commitment is only ever legitimately reached with one.
    tactic, _ = choose_tactic(
        "obtain_commitment", "promise_to_pay", CONFIDENT,
        failed_tactics=[], tactic_weights={}, is_first_contact=False, contact_target="pm",
        active_commitment_type="payment_date",
    )
    assert tactic == "confirm_promise"


def test_recover_missed_promise_prefers_reflexion_reask_on_a_tie():
    tactic, _ = choose_tactic(
        "recover_missed_promise", "promise_missed", CONFIDENT,
        failed_tactics=[], tactic_weights={}, is_first_contact=False, contact_target="pm",
    )
    assert tactic == "reflexion_reask"


def test_resolve_blocker_prefers_blocker_ack_on_a_tie():
    tactic, _ = choose_tactic(
        "resolve_blocker", "blocked", CONFIDENT,
        failed_tactics=[], tactic_weights={}, is_first_contact=False, contact_target="customer",
    )
    assert tactic == "blocker_ack"


def test_default_objective_prefers_polite_outreach_on_a_tie():
    tactic, _ = choose_tactic(
        "obtain_commitment_unknown_objective_falls_through", "overdue", CONFIDENT,
        failed_tactics=[], tactic_weights={}, is_first_contact=False, contact_target="customer",
    )
    assert tactic == "polite_outreach"


def test_a_real_learned_weight_can_still_override_the_default_priority():
    # The tie-break only applies when scores are actually equal -- a real
    # positive weight for a lower-priority candidate should still win.
    tactic, _ = choose_tactic(
        "obtain_commitment", "promise_to_pay", CONFIDENT,
        failed_tactics=[], tactic_weights={"firm_reminder": 5.0}, is_first_contact=False, contact_target="pm",
    )
    assert tactic == "firm_reminder"


def test_failed_tactic_is_still_downranked_below_an_untried_one():
    tactic, _ = choose_tactic(
        "obtain_commitment", "promise_to_pay", CONFIDENT,
        failed_tactics=["confirm_promise"], tactic_weights={}, is_first_contact=False, contact_target="pm",
    )
    assert tactic != "confirm_promise"
