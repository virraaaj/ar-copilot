"""Golden path: reset → follow-up email → reply → full TraceRun steps."""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.outcome_agent.adapters.llm_tools import ScriptedLLM
from app.outcome_agent.loop.scheduler import reset_demo
from app.outcome_agent.loop.traced_loop import run_traced_follow_up, submit_customer_reply
from app.outcome_agent.mailbox.store import MailboxStore
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.trace_store import TraceStore


def _sqlite_settings():
    """These are hermetic golden-path tests against a tmp sqlite db_path,
    using ScriptedLLM/force_mock_llm so no real LLM call happens either --
    they must not depend on live infrastructure. get_settings() alone
    isn't enough: OUTCOME_STORE_BACKEND defaults to "auto", which resolves
    to azure mode whenever this environment's DATABASE_URL is set (true on
    any machine configured to run the app against the real Azure backend),
    so the ledger silently tries a real Postgres connection regardless of
    the tmp db_path passed in. Found 2026-08-18: identical failures with
    and without that week's uncommitted guided-demo changes, i.e. this
    predates them and is a test-isolation gap, not a regression."""
    return get_settings().model_copy(update={"OUTCOME_STORE_BACKEND": "sqlite"})


REQUIRED_PHASES_OUTBOUND = {
    "signal",
    "memory.read.operational",
    "memory.read.episodic",
    "memory.read.semantic",
    "memory.read.document",
    "memory.read.graph",
    "context.built",
    "llm.plan",
    "llm.draft",
    "llm.judge",
    "guardrails",
    "mail.send",
    "memory.write",
    "graph.write",
    "state.transition",
    "schedule",
}


@pytest.mark.asyncio
async def test_traced_follow_up_emits_mandatory_phases(tmp_path):
    db = str(tmp_path / "trace.db")
    await reset_demo(db_path=db)
    store = CaseStore(db_path=db)
    cases = await store.list_all()
    # Prefer an overdue/open case
    case = next((c for c in cases if c.get("invoice_no") == "INV-7104"), cases[0])

    result = await run_traced_follow_up(
        case["id"],
        trigger="manual_follow_up",
        settings=_sqlite_settings(),
        db_path=db,
        force_mock_llm=True,
    )
    assert result.get("run_id")
    assert not result.get("skipped"), result

    run = await TraceStore(db_path=db).get_run(result["run_id"])
    assert run is not None
    phases = {s["phase"] for s in run["steps"]}
    missing = REQUIRED_PHASES_OUTBOUND - phases
    assert not missing, f"missing phases: {missing}; got {sorted(phases)}"

    # Each memory/llm/mail step has reads or writes or call
    for step in run["steps"]:
        if step["phase"].startswith("llm."):
            assert step.get("call") is not None, step["phase"]
        if step["phase"] == "mail.send":
            assert step["writes"], step

    mail = await MailboxStore(db_path=db).list_for_case(case["id"])
    assert any(m["direction"] == "outbound" for m in mail)
    assert result.get("body")


@pytest.mark.asyncio
async def test_mailbox_reply_starts_with_mail_receive(tmp_path):
    db = str(tmp_path / "trace2.db")
    await reset_demo(db_path=db)
    store = CaseStore(db_path=db)
    case = next(c for c in await store.list_all() if c.get("invoice_no") == "INV-7104")

    await run_traced_follow_up(case["id"], db_path=db, force_mock_llm=True, settings=_sqlite_settings())
    result = await submit_customer_reply(
        case["id"],
        "We will pay on 2026-08-01",
        db_path=db,
        force_mock_llm=True,
        settings=_sqlite_settings(),
    )
    assert result.get("inbound_message", {}).get("direction") == "inbound"
    run = await TraceStore(db_path=db).get_run(result["run_id"])
    phases = [s["phase"] for s in run["steps"]]
    assert "mail.receive" in phases
    assert phases.index("mail.receive") < phases.index("llm.plan")


@pytest.mark.asyncio
async def test_scripted_llm_forced_tools(tmp_path):
    db = str(tmp_path / "trace3.db")
    await reset_demo(db_path=db)
    store = CaseStore(db_path=db)
    case = next(c for c in await store.list_all() if c.get("invoice_no") == "INV-7104")

    llm = ScriptedLLM(
        [
            {
                "name": "record_next_action_plan",
                "arguments": {
                    "selected_tactic": "polite_outreach",
                    "objective": "obtain_payment_date",
                    "rationale": "scripted",
                    "candidates_considered": ["polite_outreach"],
                },
            },
            {
                "name": "record_email_draft",
                "arguments": {
                    "subject": "[AR] Invoice INV-7104",
                    "body": "Hi — could you confirm a payment date for INV-7104?",
                },
            },
            {
                "name": "record_email_judgment",
                "arguments": {
                    "passed": True,
                    "failures": [],
                    "checklist": {"asks": True},
                    "regenerate": False,
                    "notes": "ok",
                },
            },
        ]
    )
    result = await run_traced_follow_up(
        case["id"], db_path=db, llm=llm, force_mock_llm=False, settings=_sqlite_settings()
    )
    assert result.get("run_id")
    run = await TraceStore(db_path=db).get_run(result["run_id"])
    plan_step = next(s for s in run["steps"] if s["phase"] == "llm.plan")
    assert plan_step["call"]["mode"] == "live" or plan_step["call"].get("args")
    assert len(llm.calls) >= 2


@pytest.mark.asyncio
async def test_critic_blocks_threatening_draft(tmp_path):
    db = str(tmp_path / "trace4.db")
    await reset_demo(db_path=db)
    store = CaseStore(db_path=db)
    case = next(c for c in await store.list_all() if c.get("invoice_no") == "INV-7104")
    result = await run_traced_follow_up(
        case["id"],
        db_path=db,
        force_mock_llm=True,
        force_bad_draft="Pay INV-7104 now or we will pursue legal action and sue you?",
        settings=_sqlite_settings(),
    )
    # Either blocked or regenerated to a safe draft — never sends the threat
    mail = await MailboxStore(db_path=db).list_for_case(case["id"])
    for m in mail:
        assert "legal action" not in (m.get("body") or "").lower()
        assert "sue" not in (m.get("body") or "").lower()
    assert result.get("blocked") or result.get("mailbox_message")
