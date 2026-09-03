"""Tests for the 2026-09-03 automated-assistant disclosure requirement:
every outbound email to an EXTERNAL CUSTOMER must carry the approved
disclosure text; emails to the INTERNAL PM must not. See
app/outcome_agent/loop/disclosure.py for the module under test, and
app/outcome_agent/loop/executor.py / traced_loop.py for the two send
paths that wire it in.

These tests hit the real send paths (run_case_loop / run_traced_follow_up)
end-to-end against a sqlite-backed store, using the mock LLM path (no live
Azure calls) -- same pattern as test_allowlist_guard.py and
test_traced_mailbox_loop.py.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import app.outcome_agent.loop.executor as executor_mod
import app.outcome_agent.loop.traced_loop as traced_loop_mod
from app.config import get_settings
from app.outcome_agent.loop.disclosure import (
    DISCLOSURE_TEXT,
    append_disclosure,
    has_disclosure,
    is_customer_bound,
)
from app.outcome_agent.loop.executor import run_case_loop
from app.outcome_agent.loop.scheduler import reset_demo
from app.outcome_agent.loop.traced_loop import run_traced_follow_up
from app.outcome_agent.mailbox.store import MailboxStore
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.event_ledger import EventLedger
from app.outcome_agent.store.learning_store import LearningStore
from app.services.chase_guardrails import check_message_language
from tests.outcome_agent.conftest import make_settings

NOW = datetime(2026, 9, 3, 9, 0, 0)


def _stores(db_path: str):
    store = CaseStore(db_path=db_path)
    ledger = EventLedger(db_path=db_path)
    learning = LearningStore(db_path=db_path)
    return store, ledger, learning


async def _make_case(store, *, target, pm_email, customer_email, dialogue=None, case_id="case-disclosure-1"):
    row_id = await store.create(
        case_id,
        invoice_no="INV-9002",
        customer_name="Harborview Logistics",
        state="overdue",
        world={
            "invoice_no": "INV-9002",
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


def _sqlite_settings():
    """See test_traced_mailbox_loop.py's _sqlite_settings for why this is
    needed instead of bare get_settings(): OUTCOME_STORE_BACKEND defaults
    to "auto" and can resolve to azure mode depending on env, so the ledger
    would otherwise try a real Postgres connection."""
    return get_settings().model_copy(update={"OUTCOME_STORE_BACKEND": "sqlite"})


# ---------------------------------------------------------------------------
# Unit tests: disclosure.py itself
# ---------------------------------------------------------------------------


def test_append_disclosure_idempotent():
    body = "Hi there, following up on invoice INV-1."
    once = append_disclosure(body)
    twice = append_disclosure(once)
    assert once.count(DISCLOSURE_TEXT) == 1
    assert twice.count(DISCLOSURE_TEXT) == 1
    assert once == twice


def test_has_disclosure():
    assert not has_disclosure("Hi there.")
    assert has_disclosure(append_disclosure("Hi there."))


def test_is_customer_bound_matches_resolve_recipient_rule():
    # Mirrors resolve_recipient()'s own condition (communication.py):
    # target == "pm" or tactic in PM_DIRECTED_TACTICS => PM-bound, not
    # customer-bound.
    assert is_customer_bound({"target": "customer"}, "polite_outreach") is True
    assert is_customer_bound({"target": "pm"}, "polite_outreach") is False
    assert is_customer_bound({}, "pm_awareness_check") is False
    assert is_customer_bound({}, "firm_reminder") is True


def test_disclosure_text_passes_language_guard():
    """Requirement: the disclosure constant itself must not trip
    check_message_language's banned-phrase guard, or every customer-bound
    send would be blocked by our own guardrail the moment we appended it."""
    result = check_message_language(DISCLOSURE_TEXT)
    assert result.allowed is True, result.reason


# ---------------------------------------------------------------------------
# executor.py (run_case_loop) end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_customer_send_carries_disclosure_everywhere(tmp_path):
    db = str(tmp_path / "oa.db")
    store, ledger, learning = _stores(db)
    case = await _make_case(
        store,
        target="customer",
        pm_email="pm@corehelix.ai",
        customer_email="ap@customer.example",
        # Not the very first contact -- the very first touch on any case
        # routes to pm_awareness_check (PM) regardless of target.
        dialogue={"latest_outbound": "Hi team, following up on invoice INV-9002."},
    )
    settings = make_settings(STATE_DB_PATH=db)

    result = await run_case_loop(
        case, store=store, ledger=ledger, learning=learning,
        trigger="tick", now=NOW, settings=settings, mailbox=MailboxStore(db_path=db),
    )
    assert not result.get("blocked"), result

    events = await ledger.list_for_case(case["id"])
    sent = [e for e in events if e.get("kind") == "outreach_sent"]
    assert sent, "expected an outreach_sent event"
    assert has_disclosure((sent[0].get("detail") or {}).get("body") or "")

    outbox = await store.list_outbox()
    row = next(r for r in outbox if r.get("case_row_id") == case["id"])
    assert has_disclosure(row.get("body") or "")

    mail = await MailboxStore(db_path=db).list_for_case(case["id"])
    outbound = [m for m in mail if m["direction"] == "outbound"]
    assert outbound
    assert has_disclosure(outbound[0].get("body") or "")


@pytest.mark.asyncio
async def test_executor_pm_send_has_no_disclosure(tmp_path):
    db = str(tmp_path / "oa.db")
    store, ledger, learning = _stores(db)
    case = await _make_case(
        store,
        target="pm",
        pm_email="pm@corehelix.ai",
        customer_email="ap@customer.example",
        case_id="case-disclosure-pm",
    )
    settings = make_settings(STATE_DB_PATH=db)

    result = await run_case_loop(
        case, store=store, ledger=ledger, learning=learning,
        trigger="tick", now=NOW, settings=settings, mailbox=MailboxStore(db_path=db),
    )
    assert not result.get("blocked"), result

    events = await ledger.list_for_case(case["id"])
    sent = [e for e in events if e.get("kind") == "outreach_sent"]
    assert sent, "expected an outreach_sent event"
    assert not has_disclosure((sent[0].get("detail") or {}).get("body") or "")

    outbox = await store.list_outbox()
    row = next(r for r in outbox if r.get("case_row_id") == case["id"])
    assert not has_disclosure(row.get("body") or "")

    mail = await MailboxStore(db_path=db).list_for_case(case["id"])
    outbound = [m for m in mail if m["direction"] == "outbound"]
    assert outbound
    assert not has_disclosure(outbound[0].get("body") or "")


@pytest.mark.asyncio
async def test_executor_fails_closed_when_disclosure_missing(tmp_path, monkeypatch):
    """If append_disclosure is ever bypassed (simulated here by patching it
    to a no-op identity function), the pre-send check must block the send
    rather than let a customer-bound email go out without it."""
    monkeypatch.setattr(executor_mod, "append_disclosure", lambda body: body)

    db = str(tmp_path / "oa.db")
    store, ledger, learning = _stores(db)
    case = await _make_case(
        store,
        target="customer",
        pm_email="pm@corehelix.ai",
        customer_email="ap@customer.example",
        dialogue={"latest_outbound": "Hi team, following up on invoice INV-9002."},
        case_id="case-disclosure-failclosed",
    )
    settings = make_settings(STATE_DB_PATH=db)

    result = await run_case_loop(
        case, store=store, ledger=ledger, learning=learning,
        trigger="tick", now=NOW, settings=settings, mailbox=MailboxStore(db_path=db),
    )

    assert result.get("blocked") is True, result
    assert "disclosure" in (result.get("reason") or "").lower()

    events = await ledger.list_for_case(case["id"])
    assert not any(e.get("kind") == "outreach_sent" for e in events)
    mail = await MailboxStore(db_path=db).list_for_case(case["id"])
    assert not any(m["direction"] == "outbound" for m in mail)


# ---------------------------------------------------------------------------
# traced_loop.py (run_traced_follow_up) end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_traced_loop_customer_send_carries_disclosure_everywhere(tmp_path):
    db = str(tmp_path / "trace_disclosure.db")
    await reset_demo(db_path=db)
    store = CaseStore(db_path=db)
    case = next(c for c in await store.list_all() if c.get("invoice_no") == "INV-7104")
    assert case.get("target") != "pm"

    result = await run_traced_follow_up(
        case["id"], trigger="manual_follow_up", settings=_sqlite_settings(),
        db_path=db, force_mock_llm=True,
    )
    assert not result.get("blocked"), result
    assert has_disclosure(result.get("body") or "")

    mail = await MailboxStore(db_path=db).list_for_case(case["id"])
    outbound = [m for m in mail if m["direction"] == "outbound"]
    assert outbound
    assert has_disclosure(outbound[0].get("body") or "")

    outbox = await store.list_outbox()
    row = next(r for r in outbox if r.get("case_row_id") == case["id"])
    assert has_disclosure(row.get("body") or "")


@pytest.mark.asyncio
async def test_traced_loop_pm_send_has_no_disclosure(tmp_path):
    db = str(tmp_path / "trace_disclosure_pm.db")
    await reset_demo(db_path=db)
    store = CaseStore(db_path=db)
    cases = await store.list_all()
    case = next(c for c in cases if c.get("invoice_no") == "INV-7104")
    # Force this case into the PM-directed branch, mirroring how
    # resolve_recipient()/is_customer_bound() key off case["target"].
    await store.update(case["id"], target="pm", pm_email="pm@corehelix.ai")
    case = await store.get(case["id"])
    assert case.get("target") == "pm"

    result = await run_traced_follow_up(
        case["id"], trigger="manual_follow_up", settings=_sqlite_settings(),
        db_path=db, force_mock_llm=True,
    )
    assert not result.get("blocked"), result
    assert not has_disclosure(result.get("body") or "")

    mail = await MailboxStore(db_path=db).list_for_case(case["id"])
    outbound = [m for m in mail if m["direction"] == "outbound"]
    assert outbound
    assert not has_disclosure(outbound[0].get("body") or "")


@pytest.mark.asyncio
async def test_traced_loop_fails_closed_when_disclosure_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(traced_loop_mod, "append_disclosure", lambda body: body)

    db = str(tmp_path / "trace_disclosure_failclosed.db")
    await reset_demo(db_path=db)
    store = CaseStore(db_path=db)
    case = next(c for c in await store.list_all() if c.get("invoice_no") == "INV-7104")
    assert case.get("target") != "pm"

    result = await run_traced_follow_up(
        case["id"], trigger="manual_follow_up", settings=_sqlite_settings(),
        db_path=db, force_mock_llm=True,
    )

    assert result.get("blocked") is True, result
    assert "disclosure" in (result.get("reason") or "").lower()

    mail = await MailboxStore(db_path=db).list_for_case(case["id"])
    assert not any(m["direction"] == "outbound" for m in mail)
