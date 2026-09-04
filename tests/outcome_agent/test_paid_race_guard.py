"""Regression test for the 2026-09-04 fix to a confirmed race: a manual AR
aging upload (sync_invoices_to_cases) marking an invoice paid and closing
its case WHILE a background tick (run_agent_tick -> run_case_loop) is
mid-flight on that same case, awaiting the plan/draft/judge LLM calls.

Before the fix: the tick's `case` dict was fetched before the upload
landed. `check_before_send()` (guardrails.py) read that stale dict's
`world`/`state` -- still "unpaid" -- so its paid_check passed and the
chase email went out to an already-paid customer. `_persist()` then wrote
`state`/`world`/`next_action_at` straight from that same stale dict back
to the DB, REVERTING the paid closure the aging upload had just made.

This test reproduces the actual interleaving rather than unit-testing a
helper in isolation: it drives the real `run_case_loop()` (executor.py)
against a sqlite-backed CaseStore, with the LLM draft call stubbed so
that DURING that await -- the same await window the real race exploits --
it writes state=paid/world=paid straight to the DB, simulating the
concurrent aging upload. It then asserts:
  (a) no outbound email was sent (outbox, ledger, and the FakeEmailSender
      itself are all checked),
  (b) the case is still `paid` in the DB when the loop returns (the
      closure was not reverted),
  (c) the send was blocked specifically by the paid guard, not some
      other guard.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app.outcome_agent.adapters.llm_tools import ScriptedLLM
from app.outcome_agent.loop.executor import run_case_loop
from app.outcome_agent.loop.traced_loop import run_traced_follow_up
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.event_ledger import EventLedger
from app.outcome_agent.store.learning_store import LearningStore
from app.services.email_sender import FakeEmailSender
from tests.outcome_agent.conftest import make_settings

NOW = datetime(2026, 9, 4, 9, 0, 0)

PLAN_SCRIPT = {
    "name": "record_next_action_plan",
    "arguments": {
        "selected_tactic": "soft_nudge",
        "objective": "verify_payment",
        "rationale": "Customer has not confirmed a payment date yet",
        "candidates_considered": ["soft_nudge"],
    },
}
DRAFT_SCRIPT = {
    "name": "record_email_draft",
    "arguments": {
        "subject": "[AR] following up",
        "body": "Hi team, just checking in on invoice INV-7700 -- could you let us know an "
        "expected payment date?",
    },
}
JUDGE_SCRIPT = {
    "name": "record_email_judgment",
    "arguments": {"passed": True, "failures": [], "checklist": {}, "regenerate": False, "notes": "ok"},
}


class InterleavingLLM(ScriptedLLM):
    """ScriptedLLM that, the moment the DRAFT call is served, performs a
    side effect DURING that await -- writing state=paid/world=paid
    directly to the case's DB row. This is standing in for the concurrent
    AR-aging upload: from run_case_loop's point of view, its `case` dict
    was fetched before this happened and is now stale, exactly like the
    real bug.
    """

    def __init__(self, scripts, *, store: CaseStore, case_row_id: str):
        super().__init__(scripts)
        self._store = store
        self._case_row_id = case_row_id
        self.flipped = False

    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
        # Figure out whether this is about to serve the draft script
        # BEFORE popping it (ScriptedLLM.chat pops from the front).
        is_draft_call = bool(self._scripts) and self._scripts[0]["name"] == "record_email_draft"
        result = await super().chat(messages, tools=tools, tool_choice=tool_choice, return_usage=return_usage)
        if is_draft_call and not self.flipped:
            self.flipped = True
            # Simulate the concurrent aging upload landing DURING the
            # draft LLM call -- the same await window the real race
            # exploits (many yield points between the tick's initial case
            # fetch and its eventual send).
            await self._store.update(
                self._case_row_id,
                state="paid",
                world={
                    "invoice_no": "INV-7700",
                    "case_id": "paid-race-1",
                    "balance_due": 0.0,
                    "status": "paid",
                    "due_date": "2026-08-01",
                    "paid_at": NOW.isoformat(),
                },
                next_action_at=None,
            )
        return result


def _stores(db_path: str):
    store = CaseStore(db_path=db_path)
    ledger = EventLedger(db_path=db_path)
    learning = LearningStore(db_path=db_path)
    return store, ledger, learning


async def _make_unpaid_case(store: CaseStore, *, case_id: str):
    row_id = await store.create(
        case_id,
        invoice_no="INV-7700",
        customer_name="Harborview Logistics",
        state="overdue",
        world={
            "invoice_no": "INV-7700",
            "case_id": case_id,
            "balance_due": 4200.0,
            "status": "open",
            "due_date": "2026-08-01",
        },
        dialogue={},
        budget={},
        commitments=[],
        blockers=[],
        pm_email="pm@corehelix.ai",
        customer_email="ap@customer.example",
        target="customer",
    )
    return await store.get(row_id)


@pytest.mark.asyncio
async def test_run_case_loop_does_not_chase_a_case_paid_mid_tick(tmp_path, monkeypatch):
    """The core reproduction: run_case_loop() (executor.py, the tick/
    poller path) starts on an unpaid case; the case is marked paid+closed
    DURING the draft LLM await, simulating a concurrent AR-aging upload.
    No chase email must go out, and the paid closure must survive the
    tick's own persist step."""
    db = str(tmp_path / "oa.db")
    store, ledger, learning = _stores(db)
    case = await _make_unpaid_case(store, case_id="paid-race-1")
    assert case["state"] == "overdue"

    llm = InterleavingLLM(
        [PLAN_SCRIPT, DRAFT_SCRIPT, JUDGE_SCRIPT], store=store, case_row_id=case["id"]
    )
    monkeypatch.setattr(
        "app.outcome_agent.loop.executor.resolve_llm", lambda settings: (llm, False)
    )
    fake_sender = FakeEmailSender()
    monkeypatch.setattr(
        "app.services.email_sender.get_email_sender", lambda: fake_sender
    )

    result = await run_case_loop(
        case,
        store=store,
        ledger=ledger,
        learning=learning,
        trigger="tick",
        now=NOW,
        settings=make_settings(STATE_DB_PATH=db),
    )

    # The concurrent "aging upload" (the LLM double) really did fire.
    assert llm.flipped is True

    # (c) blocked, and blocked specifically by the paid guard.
    assert result.get("blocked") is True, result
    assert "paid" in (result.get("reason") or "").lower(), result

    # (a) no outbound email: outbox, ledger, and the real sender all clean.
    events = await ledger.list_for_case(case["id"])
    assert not any(e.get("kind") == "outreach_sent" for e in events), events
    assert fake_sender.sent == [], fake_sender.sent
    outbox_rows = [r for r in await store.list_outbox() if r.get("case_row_id") == case["id"]]
    assert outbox_rows == [], outbox_rows

    # (b) the paid closure was NOT reverted by this tick's persist step.
    refreshed = await store.get(case["id"])
    assert refreshed["state"] == "paid", refreshed
    assert refreshed["world"].get("status") == "paid", refreshed


PLAN_ESCALATE_SCRIPT = {
    "name": "record_next_action_plan",
    "arguments": {
        "selected_tactic": "escalation_pack",
        "objective": "escalate_handoff",
        "rationale": "Forcing the escalate-kind branch for this test",
        "candidates_considered": ["escalation_pack"],
    },
}


@pytest.mark.asyncio
async def test_persist_does_not_revert_paid_when_planner_escalates(tmp_path, monkeypatch):
    """Isolates change 3 (the terminal-state guard inside _persist) from
    change 1 (the fresh-fetch guard before check_before_send).

    When the planner selects the escalation_pack tactic, `wants_send` is
    False, so `run_case_loop` never even reaches the `check_before_send`
    call -- change 1's fresh-fetch guard is not in play at all here. The
    only thing standing between a stale in-memory `case` (which this
    branch sets to state="escalated_to_human") and the DB is `_persist`'s
    own terminal-state check. Flip the case to paid+closed during the
    draft LLM await (same interleaving as the other tests in this file),
    then assert the DB is still `paid` after the loop returns -- not
    reverted to escalated_to_human by the stale in-memory write.
    """
    db = str(tmp_path / "oa.db")
    store, ledger, learning = _stores(db)
    case = await _make_unpaid_case(store, case_id="paid-race-persist")

    llm = InterleavingLLM(
        [PLAN_ESCALATE_SCRIPT, DRAFT_SCRIPT, JUDGE_SCRIPT], store=store, case_row_id=case["id"]
    )
    monkeypatch.setattr(
        "app.outcome_agent.loop.executor.resolve_llm", lambda settings: (llm, False)
    )
    fake_sender = FakeEmailSender()
    monkeypatch.setattr(
        "app.services.email_sender.get_email_sender", lambda: fake_sender
    )

    await run_case_loop(
        case,
        store=store,
        ledger=ledger,
        learning=learning,
        trigger="tick",
        now=NOW,
        settings=make_settings(STATE_DB_PATH=db),
    )

    assert llm.flipped is True

    refreshed = await store.get(case["id"])
    assert refreshed["state"] == "paid", refreshed
    assert refreshed["world"].get("status") == "paid", refreshed


@pytest.mark.asyncio
async def test_run_traced_follow_up_does_not_chase_a_case_paid_mid_run(tmp_path, monkeypatch):
    """Same interleaving, driven through the traced_loop.py path (manual
    follow-up / customer-reply trigger) instead of the tick path -- the
    two send paths must behave identically here."""
    db = str(tmp_path / "oa.db")
    store, ledger, learning = _stores(db)
    case = await _make_unpaid_case(store, case_id="paid-race-2")

    llm = InterleavingLLM(
        [PLAN_SCRIPT, DRAFT_SCRIPT, JUDGE_SCRIPT], store=store, case_row_id=case["id"]
    )
    fake_sender = FakeEmailSender()
    monkeypatch.setattr(
        "app.services.email_sender.get_email_sender", lambda: fake_sender
    )

    result = await run_traced_follow_up(
        case["id"],
        trigger="manual_follow_up",
        settings=make_settings(STATE_DB_PATH=db),
        db_path=db,
        llm=llm,
        force_mock_llm=False,
    )

    assert llm.flipped is True
    assert result.get("blocked") is True, result
    assert "paid" in (result.get("reason") or "").lower(), result

    events = await ledger.list_for_case(case["id"])
    assert not any(e.get("kind") == "outreach_sent" for e in events), events
    assert fake_sender.sent == [], fake_sender.sent

    refreshed = await store.get(case["id"])
    assert refreshed["state"] == "paid", refreshed
    assert refreshed["world"].get("status") == "paid", refreshed
