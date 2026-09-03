"""Regression tests for the 2026-09-03 fix: the recipient allowlist -- the
safety fence meant to stop the automatic/unattended send path
(app/outcome_agent/loop/executor.py) from emailing an address it shouldn't
-- didn't actually protect that path. Two bugs, both fixed here:

(a) executor.py validated case["customer_email"] against the allowlist,
    but then sent to resolve_recipient(case, selected.tactic) -- which for
    a PM-directed tactic is the PM's address. The allowlist was checking
    the wrong address entirely.

(b) guardrails.check_before_send()'s allowlist check silently short-
    circuited (and let the send through unchecked) whenever the recipient
    was None/empty -- exactly the situation for a first PM-directed
    contact, where customer_email is commonly unset.

These tests hit the real run_case_loop() (executor.py) end-to-end against
a sqlite-backed CaseStore/EventLedger/LearningStore, using the mock LLM
path (no live Azure calls), plus a direct unit test of
guardrails.check_before_send() for the None/empty-recipient case.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.outcome_agent.loop.executor import run_case_loop
from app.outcome_agent.loop.guardrails import check_before_send
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.event_ledger import EventLedger
from app.outcome_agent.store.learning_store import LearningStore
from tests.outcome_agent.conftest import make_settings

NOW = datetime(2026, 9, 3, 9, 0, 0)


def _stores(db_path: str):
    store = CaseStore(db_path=db_path)
    ledger = EventLedger(db_path=db_path)
    learning = LearningStore(db_path=db_path)
    return store, ledger, learning


async def _make_case(store, *, target, pm_email, customer_email, dialogue=None, case_id="case-allowlist-1"):
    row_id = await store.create(
        case_id,
        invoice_no="INV-9001",
        customer_name="Harborview Logistics",
        state="overdue",
        world={
            "invoice_no": "INV-9001",
            "case_id": case_id,
            "balance_due": 1000.0,
            "status": "open",
            "due_date": "2026-08-01",
        },
        dialogue=dialogue or {},
        budget={},
        commitments=[],
        blockers=[],
        pm_email=pm_email,
        customer_email=customer_email,
        target=target,
    )
    return await store.get(row_id)


@pytest.mark.asyncio
async def test_pm_directed_send_blocked_when_pm_address_off_allowlist(tmp_path):
    """Bug (a): a PM-directed tactic (case.target == "pm") actually sends to
    pm_email, not customer_email. Before the fix, check_before_send() was
    called with recipient=case.get("customer_email") -- the customer
    address, which IS on the allowlist below -- so the guard passed even
    though the real send target (the PM address) is not on it. Revert the
    executor.py ordering fix and this test fails (result won't be
    blocked)."""
    db = str(tmp_path / "oa.db")
    store, ledger, learning = _stores(db)
    case = await _make_case(
        store,
        target="pm",
        pm_email="pm@corehelix.ai",
        customer_email="ap@customer.example",
    )
    settings = make_settings(STATE_DB_PATH=db)
    settings.outcome_agent_to_address_allowlist = ["ap@customer.example"]

    result = await run_case_loop(
        case,
        store=store,
        ledger=ledger,
        learning=learning,
        trigger="tick",
        now=NOW,
        settings=settings,
    )

    assert result.get("blocked") is True, result
    assert "allowlist" in (result.get("reason") or "").lower()

    # Nothing was actually sent.
    events = await ledger.list_for_case(case["id"])
    assert not any(e.get("kind") == "outreach_sent" for e in events)


@pytest.mark.asyncio
async def test_customer_directed_send_allowed_when_on_allowlist(tmp_path):
    """Control case: an ordinary customer-directed send to an address that
    IS on the allowlist must still go through -- the fix must not make the
    guard block legitimate sends."""
    db = str(tmp_path / "oa.db")
    store, ledger, learning = _stores(db)
    case = await _make_case(
        store,
        target="customer",
        pm_email="pm@corehelix.ai",
        customer_email="ap@customer.example",
        # Not the very first contact -- goals.choose_objective() routes the
        # very first touch on ANY case to pm_awareness_check (the PM
        # address) regardless of target, so a genuine customer-directed
        # allowlist check needs a case that's already past that.
        dialogue={"latest_outbound": "Hi team, following up on invoice INV-9001."},
        case_id="case-allowlist-2",
    )
    settings = make_settings(STATE_DB_PATH=db)
    settings.outcome_agent_to_address_allowlist = ["ap@customer.example"]

    result = await run_case_loop(
        case,
        store=store,
        ledger=ledger,
        learning=learning,
        trigger="tick",
        now=NOW,
        settings=settings,
    )

    assert not result.get("blocked"), result
    events = await ledger.list_for_case(case["id"])
    sent = [e for e in events if e.get("kind") == "outreach_sent"]
    assert sent, "expected an outreach_sent event"
    assert (sent[0].get("detail") or {}).get("recipient") == "ap@customer.example"


def test_guardrail_blocks_unknown_recipient_when_allowlist_configured():
    """Bug (b), isolated to guardrails.py: `if allowlist and recipient and
    recipient.lower() not in allowlist` used to short-circuit (and skip
    the check entirely -- send allowed) whenever recipient was falsy.
    Revert the guardrails.py fail-closed fix and this assertion flips to
    guard.allowed is True."""
    case = {"world": {"balance_due": 1000.0, "status": "open"}, "state": "overdue"}
    guard = check_before_send(
        case,
        "Hi, just checking on invoice INV-1.",
        now=NOW,
        allowlist=["ap@customer.example"],
        recipient=None,
    )
    assert guard.allowed is False
    assert "allowlist" in (guard.reason or "").lower()

    # Same with an empty string, not just None.
    guard2 = check_before_send(
        case,
        "Hi, just checking on invoice INV-1.",
        now=NOW,
        allowlist=["ap@customer.example"],
        recipient="",
    )
    assert guard2.allowed is False


def test_guardrail_allows_known_recipient_on_allowlist():
    """Sanity check: the fail-closed change must not block a recipient that
    IS on the allowlist."""
    case = {"world": {"balance_due": 1000.0, "status": "open"}, "state": "overdue"}
    guard = check_before_send(
        case,
        "Hi, just checking on invoice INV-1.",
        now=NOW,
        allowlist=["ap@customer.example"],
        recipient="ap@customer.example",
    )
    assert guard.allowed is True
