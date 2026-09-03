"""Regression tests for the 2026-09-03 fix: an escalate-kind planner
selection must never send an outbound customer email.

Diagnosed bug: action_simulator.py's `simulate_candidates()` always
includes an `escalation_pack` candidate with `kind="escalate"` (see
CandidateAction construction: `kind="send_message" if tactic !=
"escalation_pack" else "escalate"`). executor.py (run_case_loop) already
special-cased `selected.kind == "escalate"` -- it builds an
EscalationPack, sets state=escalated_to_human, and calls
send_escalation_notice() *instead of* sending an outbound email.
traced_loop.py (run_traced_follow_up) had no matching branch: an
escalate-kind selection fell straight through draft -> judge -> send and
emailed the resolved recipient -- the CUSTOMER for a non-PM-directed
tactic -- at the exact moment the agent decided it must stop and hand
off to a human.

The fix adds a matching branch to traced_loop.py, placed right after the
LLM picks the tactic (before any draft/send work happens). These tests
force the LLM planner to select the `escalation_pack` tactic via
ScriptedLLM -- independent of the budget-exhausted/objective-based
pre-plan escalation branch, which is unaffected by this bug and already
present, identically, in both files.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.outcome_agent.adapters.llm_tools import ScriptedLLM
from app.outcome_agent.loop.executor import run_case_loop
from app.outcome_agent.loop.traced_loop import run_traced_follow_up
from app.outcome_agent.mailbox.store import MailboxStore
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.event_ledger import EventLedger
from app.outcome_agent.store.learning_store import LearningStore
from tests.outcome_agent.conftest import make_settings

NOW = datetime(2026, 9, 3, 9, 0, 0)

PLAN_ESCALATE_SCRIPT = {
    "name": "record_next_action_plan",
    "arguments": {
        "selected_tactic": "escalation_pack",
        "objective": "escalate_handoff",
        "rationale": "LLM judged this case needs a human now",
        "candidates_considered": ["escalation_pack"],
    },
}

# Only needed for the executor.py path, which drafts/judges BEFORE
# checking selected.kind -- see executor.py's `wants_send` computation,
# which happens after the draft/judge loop. traced_loop.py's new branch
# (this fix) sits before drafting, so it never needs these.
DRAFT_SCRIPT = {
    "name": "record_email_draft",
    "arguments": {"subject": "[AR] escalation", "body": "placeholder draft, should never be sent"},
}
JUDGE_SCRIPT = {
    "name": "record_email_judgment",
    "arguments": {"passed": True, "failures": [], "checklist": {}, "regenerate": False, "notes": "ok"},
}


async def _make_case(store: CaseStore, *, case_id: str):
    row_id = await store.create(
        case_id,
        invoice_no="INV-9200",
        customer_name="Harborview Logistics",
        state="overdue",
        world={
            "invoice_no": "INV-9200",
            "case_id": case_id,
            "balance_due": 5000.0,
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
async def test_traced_path_escalate_kind_sends_no_outbound_email(tmp_path):
    """Reverting the traced_loop.py fix makes this fail: the run would
    reach mail.send and hand back a mailbox_message instead of escalating."""
    db = str(tmp_path / "oa.db")
    store = CaseStore(db_path=db)
    case = await _make_case(store, case_id="traced-escalate-1")

    llm = ScriptedLLM([PLAN_ESCALATE_SCRIPT])
    result = await run_traced_follow_up(
        case["id"],
        trigger="manual_follow_up",
        settings=make_settings(STATE_DB_PATH=db),
        db_path=db,
        llm=llm,
        force_mock_llm=False,
    )

    assert result.get("escalated") is True, result
    assert "mailbox_message" not in result

    refreshed = await store.get(case["id"])
    assert refreshed["state"] == "escalated_to_human"
    assert (refreshed.get("escalation") or {}).get("reason") == "Planner selected escalation handoff"

    # send_escalation_notice() legitimately emails the PM to say the agent
    # has stopped -- that's a real, expected outbound message. What must
    # NEVER happen is an outbound message to the CUSTOMER, and no
    # customer-facing "outreach_sent" ledger event at all.
    mail = await MailboxStore(db_path=db).list_for_case(case["id"])
    assert not any(
        m["direction"] == "outbound" and m.get("to_addr") == "ap@customer.example" for m in mail
    ), mail

    ledger = EventLedger(db_path=db)
    events = await ledger.list_for_case(case["id"])
    assert not any(e.get("kind") == "outreach_sent" for e in events), events


@pytest.mark.asyncio
async def test_escalate_kind_parity_between_executor_and_traced_paths(tmp_path, monkeypatch):
    """Both send paths must agree: an escalate-kind planner selection
    never emails the customer, on either the executor.py (tick/poller)
    path or the traced_loop.py (manual follow-up / reply) path."""
    db = str(tmp_path / "oa.db")
    store = CaseStore(db_path=db)
    ledger = EventLedger(db_path=db)
    learning = LearningStore(db_path=db)
    mailbox = MailboxStore(db_path=db)

    # -- executor.py path --
    # run_case_loop() has no llm= parameter; it resolves its client via
    # resolve_llm(settings) internally, so force that resolution instead.
    executor_llm = ScriptedLLM([PLAN_ESCALATE_SCRIPT, DRAFT_SCRIPT, JUDGE_SCRIPT])
    monkeypatch.setattr(
        "app.outcome_agent.loop.executor.resolve_llm", lambda settings: (executor_llm, False)
    )
    exec_case = await _make_case(store, case_id="parity-executor")
    exec_result = await run_case_loop(
        exec_case,
        store=store,
        ledger=ledger,
        learning=learning,
        trigger="tick",
        now=NOW,
        settings=make_settings(STATE_DB_PATH=db),
        mailbox=mailbox,
    )
    # run_case_loop()'s escalate-kind branch (executor.py, unmodified by
    # this fix) doesn't add an "escalated" key to its return value -- only
    # its earlier budget-hard-stop branch does. Assert on case state /
    # escalation dossier instead, which both branches set identically.
    exec_refreshed = await store.get(exec_case["id"])
    assert exec_refreshed["state"] == "escalated_to_human"
    assert (exec_refreshed.get("escalation") or {}).get("reason") == "Planner selected escalation handoff"

    # -- traced_loop.py path --
    traced_llm = ScriptedLLM([PLAN_ESCALATE_SCRIPT])
    traced_case = await _make_case(store, case_id="parity-traced")
    traced_result = await run_traced_follow_up(
        traced_case["id"],
        trigger="manual_follow_up",
        settings=make_settings(STATE_DB_PATH=db),
        db_path=db,
        llm=traced_llm,
        force_mock_llm=False,
    )
    assert traced_result.get("escalated") is True, traced_result
    traced_refreshed = await store.get(traced_case["id"])
    assert traced_refreshed["state"] == "escalated_to_human"
    assert (traced_refreshed.get("escalation") or {}).get("reason") == "Planner selected escalation handoff"

    # Neither path sent an outbound email to the CUSTOMER (the PM
    # escalation notice is a legitimate, separate outbound message), and
    # neither logged a customer-facing "outreach_sent" ledger event.
    for case_row_id in (exec_case["id"], traced_case["id"]):
        mail = await mailbox.list_for_case(case_row_id)
        assert not any(
            m["direction"] == "outbound" and m.get("to_addr") == "ap@customer.example" for m in mail
        ), mail
        events = await ledger.list_for_case(case_row_id)
        assert not any(e.get("kind") == "outreach_sent" for e in events), events
