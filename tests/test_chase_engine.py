"""Tests: chase_engine.py (added 2026-07-20, Phase C2). Real ChaseStore
(temp SQLite) + real ProjectConversationStore + FakeMessenger/
FakeEmailSender + respx-mocked backend, same pattern as
test_followup_engine.py/test_teams_bot.py. A plain object stands in for
Settings so these stay independent of app.config/env vars -- every
chase_engine function takes `settings` as an explicit argument rather
than calling get_settings() itself."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.channels.teams.messenger import FakeMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.services.backend_client import BackendClient
from app.services.chase_engine import (
    _recent_turns,
    advance_chase_with_reply,
    extract_subject_token,
    find_and_create_new_chases,
    poll_chase_mailbox,
    process_due_chases,
    run_chase_tick,
)
from app.services.chase_store import ChaseStore
from app.services.email_sender import FakeEmailSender

BASE = "http://test-backend"


def _mock_login():
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))


def make_settings(**overrides) -> SimpleNamespace:
    defaults = dict(
        CHASE_DRY_RUN=True,
        CHASE_MAX_SENDS_PER_TICK=10,
        CHASE_MAX_NUDGES=3,
        CHASE_MAX_MISSED_COMMITMENTS=3,
        CHASE_MAX_COMMITMENT_DAYS=90,
        CHASE_GRACE_DAYS=2,
        CHASE_PAYMENT_VERIFY_DAYS=3,
        CHASE_NUDGE_INTERVAL_DAYS=3,
        CHASE_MAX_CLARIFICATIONS=1,
        CHASE_MAX_POSTPONEMENTS=3,
        chase_to_address_allowlist=[],
        CHASE_COMPOSER_ENABLED=False,
        CHASE_SMART_ESCALATION_ENABLED=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def backend() -> BackendClient:
    return BackendClient(BASE, "svc@example.com", "password")


@pytest.fixture
def chase_store(tmp_path) -> ChaseStore:
    return ChaseStore(db_path=str(tmp_path / "state.db"))


@pytest.fixture
def project_store(tmp_path) -> ProjectConversationStore:
    return ProjectConversationStore(db_path=str(tmp_path / "state.db"))


@pytest.fixture
def messenger() -> FakeMessenger:
    return FakeMessenger()


@pytest.fixture
def email_sender() -> FakeEmailSender:
    return FakeEmailSender()


def _case_json(case_id="case-1", due_days_ago=5, project_number="PN-1", status="active",
                active_pause_id=None, case_key="CK-1", invoice_no="INV-1"):
    due = (date.today() - timedelta(days=due_days_ago)).isoformat()
    return {
        "id": case_id,
        "case_key": case_key,
        "case_status": status,
        "project_number": project_number,
        "project_name": "Meridian Bay",
        "primary_invoice_id": invoice_no,
        "primary_invoice_due_date": due,
        "primary_invoice_open_amount": 50000,
        "active_pause_id": active_pause_id,
    }


def past_iso(days=1) -> str:
    return (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)).isoformat()


# ---- find_and_create_new_chases -------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_creates_chase_for_overdue_active_unpaused_case(backend, chase_store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": [_case_json()]})
    )

    created = await find_and_create_new_chases(backend, chase_store)

    assert created == 1
    chase = await chase_store.get_open_for_case("case-1")
    assert chase is not None
    assert chase["state"] == "pending"
    assert chase["invoice_no"] == "INV-1"
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_skips_case_not_yet_due(backend, chase_store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": [_case_json(due_days_ago=-5)]})
    )

    created = await find_and_create_new_chases(backend, chase_store)

    assert created == 0
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_skips_paused_case(backend, chase_store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": [_case_json(active_pause_id="pause-1")]})
    )

    created = await find_and_create_new_chases(backend, chase_store)

    assert created == 0
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_skips_case_that_already_has_an_open_chase(backend, chase_store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": [_case_json()]})
    )
    await chase_store.create("case-1")

    created = await find_and_create_new_chases(backend, chase_store)

    assert created == 0
    await backend.close()


# ---- process_due_chases: pending -> awaiting_pm ---------------------------


@pytest.mark.asyncio
@respx.mock
async def test_pending_chase_sends_pm_outreach_and_resolves_pm_email(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", project_number="PN-1", invoice_no="INV-1", next_action_at=past_iso())

    settings = make_settings(CHASE_DRY_RUN=False)
    processed = await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    assert processed == 1
    chase = await chase_store.get(chase_id)
    assert chase["state"] == "awaiting_pm"
    assert chase["pm_email"] == "pm@corehelix.ai"
    # No Teams chat known for PN-1 -- falls back to email.
    assert len(email_sender.sent) == 1
    assert email_sender.sent[0].to == "pm@corehelix.ai"
    assert chase["subject_token"] in email_sender.sent[0].subject
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_pending_chase_prefers_teams_when_project_chat_known(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    await project_store.record("PN-1", "conv-1")
    await chase_store.create("case-1", project_number="PN-1", next_action_at=past_iso())

    settings = make_settings(CHASE_DRY_RUN=False)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    assert len(messenger.sent) == 1
    assert messenger.sent[0].conversation_id == "conv-1"
    assert email_sender.sent == []
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_does_not_actually_send_anything(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    await chase_store.create("case-1", project_number="PN-1", next_action_at=past_iso())

    settings = make_settings(CHASE_DRY_RUN=True)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    assert messenger.sent == []
    assert email_sender.sent == []
    await backend.close()


# ---- process_due_chases: nudge / escalate ---------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_awaiting_pm_no_reply_sends_a_nudge(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", project_number="PN-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai", nudge_count=0)

    settings = make_settings(CHASE_DRY_RUN=False)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    chase = await chase_store.get(chase_id)
    assert chase["nudge_count"] == 1
    assert chase["state"] == "awaiting_pm"
    assert len(email_sender.sent) == 1
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_blocked_chase_due_for_checkin_sends_a_status_request(backend, chase_store, project_store, messenger, email_sender):
    """Regression test (added 2026-07-25): before the 'blocked' state was
    wired into _process_one_due_chase's dispatch, a blocked chase whose
    next_action_at arrived hit the `else: return` fallthrough and silently
    did nothing -- the PM/customer never heard from the agent again."""
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", project_number="PN-1", next_action_at=past_iso())
    await chase_store.update(
        chase_id, state="blocked", target="pm", pm_email="pm@corehelix.ai",
        blocker_type="approval_pending", postpone_count=0,
    )

    settings = make_settings(CHASE_DRY_RUN=False)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    chase = await chase_store.get(chase_id)
    assert chase["postpone_count"] == 1
    assert chase["state"] == "blocked"
    assert len(email_sender.sent) == 1
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_awaiting_pm_escalates_once_nudge_budget_exhausted(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", project_number="PN-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai", nudge_count=3)

    settings = make_settings(CHASE_DRY_RUN=False, CHASE_MAX_NUDGES=3)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "escalated"
    events = await chase_store.list_events(chase_id)
    assert any(e["kind"] == "escalated" for e in events)
    await backend.close()


# ---- process_due_chases: commitment due -----------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_commitment_due_and_paid_closes_chase(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(
        return_value=httpx.Response(200, json=_case_json(status="closed_paid"))
    )
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="commitment_tracked", promised_by="pm", target="pm",
                              promised_date=(date.today() - timedelta(days=1)).isoformat())

    settings = make_settings(CHASE_DRY_RUN=False)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "closed_paid"
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_commitment_due_and_unpaid_rechases_and_increments_missed_count(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", project_number="PN-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="commitment_tracked", promised_by="pm", target="pm", pm_email="pm@corehelix.ai",
                              promised_date=(date.today() - timedelta(days=1)).isoformat(), missed_count=0)

    settings = make_settings(CHASE_DRY_RUN=False)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "awaiting_pm"
    assert chase["missed_count"] == 1
    assert len(email_sender.sent) == 1
    await backend.close()


# ---- generic "already paid" short-circuit ---------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_any_due_chase_closes_immediately_if_case_already_paid(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(
        return_value=httpx.Response(200, json=_case_json(status="closed_paid"))
    )
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", nudge_count=1)

    settings = make_settings(CHASE_DRY_RUN=False)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "closed_paid"
    assert email_sender.sent == []  # no nudge sent -- short-circuited before dispatch
    await backend.close()


# ---- per-tick send budget ---------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_send_budget_defers_extra_sends_to_next_tick(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json(case_id="case-1")))
    respx.get(f"{BASE}/api/v2/dunning/cases/case-2").mock(return_value=httpx.Response(200, json=_case_json(case_id="case-2")))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    await chase_store.create("case-1", project_number="PN-1", next_action_at=past_iso())
    await chase_store.create("case-2", project_number="PN-1", next_action_at=past_iso())

    settings = make_settings(CHASE_DRY_RUN=False, CHASE_MAX_SENDS_PER_TICK=1)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    assert len(email_sender.sent) == 1
    await backend.close()


# ---- allowlist ---------------------------------------------------------------


# ---- Policy and Guardrail Engine (added 2026-07-28, spec §6.15) -----------


@pytest.mark.asyncio
@respx.mock
async def test_guardrail_blocks_message_containing_banned_language(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai", nudge_count=0)

    # Force the deterministic template itself to contain banned language by
    # monkeypatching chase_machine's on_nudge_check output would be brittle;
    # instead verify the guardrail directly blocks via the composer path is
    # covered by chase_guardrails unit tests. Here we confirm the engine
    # actually calls the check and blocks -- easiest lever is DRY_RUN off
    # with a chase whose target text we control via a manual SendMessage.
    from app.services.chase_engine import _send_message
    from app.services.chase_machine import SendMessage

    chase = await chase_store.get(chase_id)
    settings = make_settings(CHASE_DRY_RUN=False)
    action = SendMessage(target="pm", kind="nudge", text="We will pursue legal action if this isn't paid.")

    await _send_message(action, chase, backend, messenger, email_sender, project_store, chase_store, settings)

    assert email_sender.sent == []
    events = await chase_store.list_events(chase_id)
    outreach = [e for e in events if e["kind"] == "outreach_sent"][0]
    assert outreach["detail"]["channel"] == "blocked"
    assert "legal action" in outreach["detail"]["error"].lower()
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_guardrail_blocks_outreach_on_a_configured_blackout_date(backend, chase_store, project_store, messenger, email_sender):
    from app.services import chase_guardrails
    from app.services.chase_engine import _send_message
    from app.services.chase_machine import SendMessage
    from app.services import sim_clock

    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai")
    chase = await chase_store.get(chase_id)

    today = (await sim_clock.now(chase_store.db_path)).date().isoformat()
    original = list(chase_guardrails.BLACKOUT_DATES)
    chase_guardrails.BLACKOUT_DATES.append(today)
    try:
        settings = make_settings(CHASE_DRY_RUN=False)
        action = SendMessage(target="pm", kind="nudge", text="Just checking in on this invoice.")
        await _send_message(action, chase, backend, messenger, email_sender, project_store, chase_store, settings)
    finally:
        chase_guardrails.BLACKOUT_DATES[:] = original

    assert email_sender.sent == []
    events = await chase_store.list_events(chase_id)
    outreach = [e for e in events if e["kind"] == "outreach_sent"][0]
    assert outreach["detail"]["channel"] == "blocked"
    assert "blackout" in outreach["detail"]["error"].lower()
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_guardrail_escalates_high_dollar_invoice_before_first_outreach(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": [_case_json(due_days_ago=5)]})
    )
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))

    created = await find_and_create_new_chases(backend, chase_store)
    assert created == 1
    chase = await chase_store.get_open_for_case("case-1")
    await chase_store.update(chase["id"], amount=150000.0)

    settings = make_settings(CHASE_DRY_RUN=False)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    chase = await chase_store.get(chase["id"])
    assert chase["state"] == "escalated"
    assert email_sender.sent == []
    events = await chase_store.list_events(chase["id"])
    assert any(e["kind"] == "escalated" and e["detail"]["reason"] == "human_approval_required" for e in events)
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_allowlist_blocks_non_listed_customer_email(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="awaiting_customer", target="customer", customer_email="blocked@x.com", nudge_count=0)

    settings = make_settings(CHASE_DRY_RUN=False, chase_to_address_allowlist=["allowed@x.com"])
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    assert email_sender.sent == []
    events = await chase_store.list_events(chase_id)
    outreach = [e for e in events if e["kind"] == "outreach_sent"][0]
    assert "allowlist" in outreach["detail"]["error"].lower()
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_failed_send_retries_soon_instead_of_silently_waiting_out_the_nudge_interval(
    backend, chase_store, project_store, messenger, email_sender
):
    """Regression test (added 2026-07-24): chase_machine already decided
    to advance the chase (e.g. into awaiting_customer with a next_action_at
    days out) before the send was even attempted -- if the send then fails,
    that decision was made on a false premise (the recipient never actually
    got anything). Left alone the chase silently waits out the full nudge
    interval as if it had. A failed send must schedule a near-term retry
    instead of trusting a message that never arrived."""
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="awaiting_customer", target="customer", customer_email="blocked@x.com", nudge_count=0)

    settings = make_settings(CHASE_DRY_RUN=False, chase_to_address_allowlist=["allowed@x.com"])
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    chase = await chase_store.get(chase_id)
    retry_at = datetime.fromisoformat(chase["next_action_at"])
    assert retry_at < datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=3)
    await backend.close()


# ---- advance_chase_with_reply ------------------------------------------------


def make_tool_call(name: str, arguments: dict):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class ScriptedLLM:
    def __init__(self, response, tokens=0):
        self._response = response
        self._tokens = tokens
        self.calls = []

    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        return self._response, self._tokens


@pytest.mark.asyncio
@respx.mock
async def test_advance_chase_with_reply_tracks_commitment(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai")
    chase = await chase_store.get(chase_id)

    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "commitment_date", "confidence": "high",
            "promised_date": (date.today() + timedelta(days=10)).isoformat(),
        })
    ]))
    settings = make_settings(CHASE_DRY_RUN=False)

    parsed = await advance_chase_with_reply(
        chase, "we'll pay in 10 days", llm, backend, messenger, email_sender, chase_store, project_store, settings
    )

    assert parsed.intent == "commitment_date"
    updated = await chase_store.get(chase_id)
    assert updated["state"] == "commitment_tracked"


# ---- _recent_turns / wrong-recipient regression (added 2026-07-24) --------
# A PM said "check with the customer email I provided" (a back-reference,
# no address in that reply). The only address literally present in the raw
# text was the PM's own, from their client's auto-quoted "On ... wrote:"
# line -- the parser picked that up as "the customer's email" and outreach
# went to the wrong person. Fix: strip quoted content before parsing, and
# feed the parser this chase's recent history so it can actually resolve
# the back-reference instead of guessing from what's left in the quote.


@pytest.mark.asyncio
async def test_recent_turns_builds_history_from_outreach_and_reply_events(chase_store):
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    await chase_store.add_event(chase_id, "outreach_sent", {"target": "pm", "text": "Is there a payment date?"})
    await chase_store.add_event(chase_id, "reply_received", {"target": "pm", "text": "Reach out to the customer at x@y.com"})
    # action_decided logs the same text as the outreach_sent right after it --
    # must not appear twice in the reconstructed history.
    await chase_store.add_event(chase_id, "action_decided", {"target": "pm", "text": "Is there a payment date?"})

    turns = await _recent_turns(chase_store, chase_id)

    assert turns == [
        {"role": "assistant", "content": "Is there a payment date?"},
        {"role": "user", "content": "Reach out to the customer at x@y.com"},
    ]


@pytest.mark.asyncio
async def test_recent_turns_strips_quoted_content_from_replies(chase_store):
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    quoted_reply = (
        "Reach out to the customer at x@y.com "
        "On Thu, Jul 23, 2026 at 5:11PM Viraj Yadav <viraj.yadav@corehelix.ai> wrote: prior text"
    )
    await chase_store.add_event(chase_id, "reply_received", {"target": "pm", "text": quoted_reply})

    turns = await _recent_turns(chase_store, chase_id)

    assert turns == [{"role": "user", "content": "Reach out to the customer at x@y.com"}]


@pytest.mark.asyncio
async def test_recent_turns_capped_to_last_pairs(chase_store):
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    for i in range(5):
        await chase_store.add_event(chase_id, "outreach_sent", {"target": "pm", "text": f"question {i}"})
        await chase_store.add_event(chase_id, "reply_received", {"target": "pm", "text": f"answer {i}"})

    turns = await _recent_turns(chase_store, chase_id, max_pairs=2)

    assert len(turns) == 4
    assert turns[0]["content"] == "question 3"
    assert turns[-1]["content"] == "answer 4"


@pytest.mark.asyncio
@respx.mock
async def test_advance_chase_with_reply_resolves_a_back_reference_using_history(
    backend, chase_store, project_store, messenger, email_sender
):
    """End-to-end: without recent-turn history and quote-stripping, this
    exact scenario is how the wrong-recipient bug happened live."""
    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai")
    await chase_store.add_event(chase_id, "outreach_sent", {"target": "pm", "text": "Is there a payment date?"})
    await chase_store.add_event(
        chase_id, "reply_received", {"target": "pm", "text": "Reach out to the customer at sachin.mahishi@corehelix.ai"}
    )
    chase = await chase_store.get(chase_id)

    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "handoff_to_customer", "confidence": "high",
            "customer_contact_email": "sachin.mahishi@corehelix.ai",
        })
    ]))
    settings = make_settings(CHASE_DRY_RUN=False)
    reply_with_quote = (
        "check with the customer email I provided in these emails "
        "On Thu, Jul 23, 2026 at 5:19PM Viraj Yadav <viraj.yadav@corehelix.ai> wrote: prior text"
    )

    await advance_chase_with_reply(
        chase, reply_with_quote, llm, backend, messenger, email_sender, chase_store, project_store, settings
    )

    # The model was given history + a cleaned reply and (per the script)
    # correctly resolved the real customer address -- not the PM's own
    # address that was sitting in the quote.
    sent_messages = llm.calls[0]["messages"]
    joined = " ".join(m["content"] for m in sent_messages)
    assert "sachin.mahishi@corehelix.ai" in joined  # from history
    assert "viraj.yadav@corehelix.ai" not in joined  # quote stripped out

    updated = await chase_store.get(chase_id)
    assert updated["customer_email"] == "sachin.mahishi@corehelix.ai"
    assert updated["customer_email"] != "viraj.yadav@corehelix.ai"


@pytest.mark.asyncio
@respx.mock
async def test_advance_chase_with_reply_out_of_scope_redirects_to_last_question_asked(
    backend, chase_store, project_store, messenger, email_sender
):
    """Regression test (added 2026-07-24): the customer had just been asked
    'when's a good time to follow up' (a checkback ack) and instead asked
    about other customers -- the redirect must repeat that actual last
    question, not a hardcoded payment-date line that was never asked."""
    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    await chase_store.update(
        chase_id, state="awaiting_customer", target="customer", customer_email="cust@x.com", clarify_count=0
    )
    await chase_store.add_event(
        chase_id, "outreach_sent",
        {"target": "customer", "text": "No worries -- when would be a good time for me to follow up on this?"},
    )
    chase = await chase_store.get(chase_id)

    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "out_of_scope_request", "confidence": "high"})
    ]))
    settings = make_settings(CHASE_DRY_RUN=True)

    await advance_chase_with_reply(
        chase, "Are you seeing other customers who are not paying on time?",
        llm, backend, messenger, email_sender, chase_store, project_store, settings,
    )

    events = await chase_store.list_events(chase_id)
    sent = [e for e in events if e["kind"] == "dry_run_send"][0]
    text = sent["detail"]["text"].lower()
    assert "other customers" in text
    assert "when would be a good time" in text
    assert "payment by" not in text


@pytest.mark.asyncio
@respx.mock
async def test_advance_chase_with_reply_resolves_handoff_to_contact_role(
    backend, chase_store, project_store, messenger, email_sender
):
    """PM says "ask BU Finance" -- the engine (not the pure chase_machine)
    is responsible for looking that role up against the project's real
    contacts before the state machine can route to it."""
    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={
            "project_number": "PN-1",
            "contacts": [
                {"contact_type": "pm", "email": "pm@x.com"},
                {"contact_type": "bu_finance", "email": "finance@x.com"},
            ],
        })
    )
    chase_id = await chase_store.create("case-1", invoice_no="INV-1", project_number="PN-1")
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@x.com")
    chase = await chase_store.get(chase_id)

    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "handoff_to_contact", "confidence": "high", "contact_role": "bu_finance",
        })
    ]))
    settings = make_settings(CHASE_DRY_RUN=False)

    parsed = await advance_chase_with_reply(
        chase, "ask BU Finance about this", llm, backend, messenger, email_sender, chase_store, project_store, settings
    )

    assert parsed.intent == "handoff_to_contact"
    updated = await chase_store.get(chase_id)
    assert updated["state"] == "awaiting_contact"
    assert updated["target"] == "bu_finance"
    assert updated["contact_email"] == "finance@x.com"
    events = await chase_store.list_events(chase_id)
    assert any(e["kind"] == "handoff_to_contact" for e in events)
    assert any(e["kind"] == "outreach_sent" and e["detail"]["channel"] == "email" for e in events)


@pytest.mark.asyncio
@respx.mock
async def test_advance_chase_with_reply_handoff_to_unknown_role_clarifies(
    backend, chase_store, project_store, messenger, email_sender
):
    """The project has no legal contact on file -- must not invent a
    routing target with nowhere to actually send."""
    _mock_login()
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"project_number": "PN-1", "contacts": [{"contact_type": "pm", "email": "pm@x.com"}]})
    )
    chase_id = await chase_store.create("case-1", invoice_no="INV-1", project_number="PN-1")
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@x.com")
    chase = await chase_store.get(chase_id)

    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "handoff_to_contact", "confidence": "high", "contact_role": "legal",
        })
    ]))
    settings = make_settings(CHASE_DRY_RUN=False)

    await advance_chase_with_reply(
        chase, "that's a legal question", llm, backend, messenger, email_sender, chase_store, project_store, settings
    )

    updated = await chase_store.get(chase_id)
    assert updated["state"] != "awaiting_contact"
    events = await chase_store.list_events(chase_id)
    assert any(e["kind"] == "clarify_requested" for e in events)
    assert any(e["kind"] == "reply_received" for e in events)
    await backend.close()


# ---- run_chase_tick end-to-end ------------------------------------------------


# ---- extract_subject_token / poll_chase_mailbox (Phase C3) ------------------


def test_extract_subject_token_finds_it_in_a_subject_line():
    assert extract_subject_token("[AR-7Q2F1A] Re: invoice INV-1") == "AR-7Q2F1A"


def test_extract_subject_token_returns_none_when_absent():
    assert extract_subject_token("Re: invoice INV-1") is None


def test_extract_subject_token_does_not_match_a_bare_invoice_number():
    assert extract_subject_token("Re: INV-1 payment") is None


class FakeMailboxReader:
    def __init__(self, messages):
        self._messages = messages

    async def list_recent_messages(self, top=25):
        return self._messages


@pytest.mark.asyncio
async def test_poll_chase_mailbox_returns_zero_when_reader_is_none(backend, chase_store, project_store, messenger, email_sender):
    settings = make_settings(CHASE_DRY_RUN=False)
    result = await poll_chase_mailbox(None, None, backend, messenger, email_sender, chase_store, project_store, settings)
    assert result == 0


@pytest.mark.asyncio
@respx.mock
async def test_poll_chase_mailbox_matches_by_subject_token_and_advances_chase(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    await chase_store.update(chase_id, state="awaiting_customer", target="customer", customer_email="cust@x.com")
    chase = await chase_store.get(chase_id)

    reader = FakeMailboxReader([
        {"id": "msg-1", "subject": f"Re: [{chase['subject_token']}] invoice", "bodyPreview": "We'll pay by end of week."},
    ])
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "no_commitment", "confidence": "low"})
    ]))
    settings = make_settings(CHASE_DRY_RUN=False)

    processed = await poll_chase_mailbox(reader, llm, backend, messenger, email_sender, chase_store, project_store, settings)

    assert processed == 1
    assert await chase_store.is_mail_processed("msg-1") is True
    events = await chase_store.list_events(chase_id)
    assert any(e["kind"] == "reply_received" for e in events)
    await backend.close()


@pytest.mark.asyncio
async def test_poll_chase_mailbox_matches_replies_to_a_contact_role_handoff(backend, chase_store, project_store, messenger, email_sender):
    """Regression test (added 2026-07-23): a chase handed off to a
    project-contact role (e.g. BU Finance) sits in state='awaiting_contact',
    not 'awaiting_pm'/'awaiting_customer' -- the mail poller's eligibility
    check originally only allowed those two, so a real reply from BU
    Finance was silently dropped and marked processed without ever being
    interpreted."""
    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    await chase_store.update(chase_id, state="awaiting_contact", target="bu_finance", contact_email="finance@x.com")
    chase = await chase_store.get(chase_id)

    reader = FakeMailboxReader([
        {
            "id": "msg-1",
            "subject": f"Re: [{chase['subject_token']}] invoice",
            "bodyPreview": "We'll pay by end of week.",
            "from": {"emailAddress": {"address": "finance@x.com"}},
        },
    ])
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "no_commitment", "confidence": "low"})
    ]))
    settings = make_settings(CHASE_DRY_RUN=False)

    processed = await poll_chase_mailbox(reader, llm, backend, messenger, email_sender, chase_store, project_store, settings)

    assert processed == 1
    events = await chase_store.list_events(chase_id)
    assert any(e["kind"] == "reply_received" for e in events)
    await backend.close()


@pytest.mark.asyncio
async def test_poll_chase_mailbox_matches_replies_to_a_blocked_chase(backend, chase_store, project_store, messenger, email_sender):
    """Regression test (added 2026-07-27, found live): a customer replied
    "in two days" to the blocker check-in question ("when should I check
    back?"), but the chase was in state='blocked' -- not in the mail
    poller's eligibility tuple -- so the reply was silently marked
    processed and dropped without ever reaching chase_machine. Same class
    of bug as the awaiting_contact fix above, different state."""
    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    await chase_store.update(
        chase_id, state="blocked", target="customer", customer_email="cust@x.com",
        blocker_type="approval_pending", blocker_description="Checking with team on SLAs",
    )
    chase = await chase_store.get(chase_id)

    reader = FakeMailboxReader([
        {"id": "msg-1", "subject": f"Re: [{chase['subject_token']}] invoice", "bodyPreview": "In two days."},
    ])
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "checkback_requested", "confidence": "high",
            "followup_date": (date.today() + timedelta(days=2)).isoformat(),
        })
    ]))
    settings = make_settings(CHASE_DRY_RUN=False)

    processed = await poll_chase_mailbox(reader, llm, backend, messenger, email_sender, chase_store, project_store, settings)

    assert processed == 1
    events = await chase_store.list_events(chase_id)
    assert any(e["kind"] == "reply_received" for e in events)
    assert any(e["kind"] == "checkback_scheduled" for e in events)
    await backend.close()


@pytest.mark.asyncio
async def test_poll_chase_mailbox_ignores_self_addressed_mail(backend, chase_store, project_store, messenger, email_sender):
    """Regression test (added 2026-07-23): a chase escalated after the
    poller re-ingested its own outreach as a "reply" -- self-addressed
    mail (PM/customer contact happens to be the same mailbox the engine
    sends from, common in this UAT setup) lands right back in the same
    inbox. A message From the engine's own EMAIL_FROM_ADDRESS must never
    be treated as an inbound reply, no matter its subject token."""
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@x.com")
    chase = await chase_store.get(chase_id)

    reader = FakeMailboxReader([
        {
            "id": "msg-1",
            "subject": f"Re: [{chase['subject_token']}] invoice",
            "bodyPreview": "Following up -- still hoping to hear back.",
            "from": {"emailAddress": {"address": "info@corehelix.ai"}},
        },
    ])
    settings = make_settings(CHASE_DRY_RUN=False, EMAIL_FROM_ADDRESS="info@corehelix.ai")

    processed = await poll_chase_mailbox(reader, None, backend, messenger, email_sender, chase_store, project_store, settings)

    assert processed == 0
    assert await chase_store.is_mail_processed("msg-1") is True
    events = await chase_store.list_events(chase_id)
    assert not any(e["kind"] == "reply_received" for e in events)
    # State/clarify budget must be untouched -- the message was ignored
    # outright, not processed as an ambiguous reply.
    unchanged = await chase_store.get(chase_id)
    assert unchanged["state"] == "awaiting_pm"
    assert unchanged["clarify_count"] == 0


@pytest.mark.asyncio
async def test_poll_chase_mailbox_case_insensitive_self_address_match(backend, chase_store, project_store, messenger, email_sender):
    chase_id = await chase_store.create("case-1", invoice_no="INV-1")
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@x.com")
    chase = await chase_store.get(chase_id)

    reader = FakeMailboxReader([
        {
            "id": "msg-1",
            "subject": f"[{chase['subject_token']}] invoice",
            "bodyPreview": "hi",
            "from": {"emailAddress": {"address": "INFO@COREHELIX.AI"}},
        },
    ])
    settings = make_settings(CHASE_DRY_RUN=False, EMAIL_FROM_ADDRESS="info@corehelix.ai")

    processed = await poll_chase_mailbox(reader, None, backend, messenger, email_sender, chase_store, project_store, settings)

    assert processed == 0


@pytest.mark.asyncio
async def test_poll_chase_mailbox_skips_messages_with_no_recognizable_token(backend, chase_store, project_store, messenger, email_sender):
    reader = FakeMailboxReader([{"id": "msg-1", "subject": "Unrelated email", "bodyPreview": "hi"}])
    settings = make_settings(CHASE_DRY_RUN=False)

    processed = await poll_chase_mailbox(reader, None, backend, messenger, email_sender, chase_store, project_store, settings)

    assert processed == 0
    assert await chase_store.is_mail_processed("msg-1") is True  # marked so it's not re-checked forever


@pytest.mark.asyncio
async def test_poll_chase_mailbox_skips_chase_already_past_awaiting_state(backend, chase_store, project_store, messenger, email_sender):
    chase_id = await chase_store.create("case-1")
    await chase_store.update(chase_id, state="commitment_tracked")
    chase = await chase_store.get(chase_id)

    reader = FakeMailboxReader([{"id": "msg-1", "subject": f"[{chase['subject_token']}] update", "bodyPreview": "paid"}])
    settings = make_settings(CHASE_DRY_RUN=False)

    processed = await poll_chase_mailbox(reader, None, backend, messenger, email_sender, chase_store, project_store, settings)

    assert processed == 0
    assert await chase_store.is_mail_processed("msg-1") is True


@pytest.mark.asyncio
async def test_poll_chase_mailbox_does_not_reprocess_the_same_message(backend, chase_store, project_store, messenger, email_sender):
    reader = FakeMailboxReader([{"id": "msg-1", "subject": "no token here", "bodyPreview": "hi"}])
    settings = make_settings(CHASE_DRY_RUN=False)

    first = await poll_chase_mailbox(reader, None, backend, messenger, email_sender, chase_store, project_store, settings)
    second = await poll_chase_mailbox(reader, None, backend, messenger, email_sender, chase_store, project_store, settings)

    assert first == 0
    assert second == 0


@pytest.mark.asyncio
@respx.mock
async def test_run_chase_tick_creates_and_processes_in_one_call(backend, chase_store, project_store, messenger, email_sender):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case_json()]}))
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))

    settings = make_settings(CHASE_DRY_RUN=False)
    result = await run_chase_tick(backend, messenger, email_sender, chase_store, project_store, settings)

    assert result >= 1
    chase = await chase_store.get_open_for_case("case-1")
    assert chase["state"] == "awaiting_pm"
    await backend.close()


# ---- simulation clock (added 2026-07-25, long-horizon outcome agent spec) --


@pytest.mark.asyncio
@respx.mock
async def test_not_yet_due_invoice_becomes_eligible_after_advancing_the_sim_clock(backend, chase_store):
    """End-to-end proof the sim clock actually drives chase-creation
    timing, not just chase_machine's internal date math: an invoice due 2
    days from now must NOT get a chase today, but must after the demo
    clock is advanced 3 days."""
    from app.services import sim_clock

    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": [_case_json(due_days_ago=-2)]})
    )

    created_before = await find_and_create_new_chases(backend, chase_store)
    assert created_before == 0
    assert await chase_store.get_open_for_case("case-1") is None

    await sim_clock.advance_days(3, chase_store.db_path)

    created_after = await find_and_create_new_chases(backend, chase_store)
    assert created_after == 1
    assert await chase_store.get_open_for_case("case-1") is not None
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_process_due_chases_uses_the_sim_clock_not_real_time(backend, chase_store, project_store, messenger, email_sender):
    """A chase whose next_action_at is 2 days from real-world now must not
    be picked up by a normal tick, but must once the sim clock has been
    advanced past it."""
    from app.services import sim_clock

    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    future_next_action = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2)).isoformat()
    chase_id = await chase_store.create("case-1", project_number="PN-1", next_action_at=future_next_action)
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai", nudge_count=0)

    settings = make_settings(CHASE_DRY_RUN=False)
    processed_before = await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)
    assert processed_before == 0

    await sim_clock.advance_days(3, chase_store.db_path)

    processed_after = await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)
    assert processed_after == 1
    chase = await chase_store.get(chase_id)
    assert chase["nudge_count"] == 1
    await backend.close()


# ---- temporal knowledge graph (added 2026-07-25) ---------------------------


@pytest.mark.asyncio
@respx.mock
async def test_blocker_then_commitment_writes_and_supersedes_graph_facts(
    backend, chase_store, project_store, messenger, email_sender
):
    """End-to-end proof of the spec's own worked example: an invoice used
    to be blocked by an approval, then got a payment promise instead --
    the graph must show the commitment as current AND still have the
    (now closed) blocker fact, linked by COMMITMENT_SUPERSEDES."""
    from app.services.graph_store import GraphStore

    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", invoice_no="INV-1", project_number="PN-1")
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai")
    chase = await chase_store.get(chase_id)

    blocker_llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "blocker_reported", "confidence": "high", "blocker_type": "approval_pending",
            "blocker_description": "Waiting on plant manager approval.",
        })
    ]))
    settings = make_settings(CHASE_DRY_RUN=True)
    await advance_chase_with_reply(
        chase, "We are waiting for the plant manager to approve it.",
        blocker_llm, backend, messenger, email_sender, chase_store, project_store, settings,
    )

    graph = GraphStore(db_path=chase_store.db_path)
    invoice_id = "invoice:INV-1"
    blocked_edges = await graph.list_edges(invoice_id, relationship="INVOICE_BLOCKED_BY", active_only=True)
    assert len(blocked_edges) == 1
    blocker_node_id = blocked_edges[0]["to_node_id"]
    blocker_node = await graph.get_node(blocker_node_id)
    assert blocker_node["type"] == "Blocker"

    # Also confirm the Customer(project)-has-Invoice fact was written.
    project_edges = await graph.list_edges("project:PN-1", relationship="CUSTOMER_HAS_INVOICE", active_only=True)
    assert any(e["to_node_id"] == invoice_id for e in project_edges)

    chase = await chase_store.get(chase_id)
    commitment_llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "commitment_date", "confidence": "high",
            "promised_date": (date.today() + timedelta(days=5)).isoformat(),
        })
    ]))
    await advance_chase_with_reply(
        chase, "Approved now. We will pay this Friday.",
        commitment_llm, backend, messenger, email_sender, chase_store, project_store, settings,
    )

    active_blocked = await graph.list_edges(invoice_id, relationship="INVOICE_BLOCKED_BY", active_only=True)
    assert active_blocked == []  # resolved, not deleted -- still queryable below
    all_blocked = await graph.list_edges(invoice_id, relationship="INVOICE_BLOCKED_BY", active_only=False)
    assert len(all_blocked) == 1
    assert all_blocked[0]["valid_to"] is not None

    active_commitment = await graph.list_edges(invoice_id, relationship="INVOICE_HAS_COMMITMENT", active_only=True)
    assert len(active_commitment) == 1
    commitment_node_id = active_commitment[0]["to_node_id"]

    supersede_edges = await graph.list_edges(commitment_node_id, relationship="COMMITMENT_SUPERSEDES", active_only=False)
    assert len(supersede_edges) == 1
    assert supersede_edges[0]["to_node_id"] == blocker_node_id

    await backend.close()


# ---- AI features: composer + smart escalation + token tracking (added 2026-07-22) ---


class QueueLLM:
    """Returns pre-scripted (message, tokens) responses in call order --
    both compose_message and assess_chase_trajectory always call with
    return_usage=True, so every response here is the tuple shape."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
        self.calls.append({"messages": messages, "tools": tools})
        return self._responses.pop(0)


@pytest.mark.asyncio
@respx.mock
async def test_composer_rewrites_outreach_and_charges_tokens_when_enabled(
    backend, chase_store, project_store, messenger, email_sender
):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", project_number="PN-1", invoice_no="INV-1", next_action_at=past_iso())

    # Must mention the invoice number to pass chase_evaluator's checklist
    # (added 2026-07-28) -- "Hey! Just checking in" alone doesn't.
    llm = QueueLLM([(SimpleNamespace(content="Hey! Just checking in on invoice INV-1 -- any update?"), 73)])
    settings = make_settings(CHASE_DRY_RUN=False, CHASE_COMPOSER_ENABLED=True)

    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings, llm)

    assert email_sender.sent[0].html_body == "<p>Hey! Just checking in on invoice INV-1 -- any update?</p>"
    chase = await chase_store.get(chase_id)
    assert chase["total_tokens_used"] == 73
    events = await chase_store.list_events(chase_id)
    outreach = [e for e in events if e["kind"] == "outreach_sent"][0]
    assert outreach["detail"]["composed"] is True
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_composer_disabled_by_default_sends_the_template_unchanged(
    backend, chase_store, project_store, messenger, email_sender
):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"contacts": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    await chase_store.create("case-1", project_number="PN-1", invoice_no="INV-1", next_action_at=past_iso())

    # No llm passed at all -- proves the composer path is fully inert by default.
    settings = make_settings(CHASE_DRY_RUN=False, CHASE_COMPOSER_ENABLED=False)
    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings)

    assert "now overdue" in email_sender.sent[0].html_body
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_smart_escalation_escalates_early_on_concerning_verdict(
    backend, chase_store, project_store, messenger, email_sender
):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    # nudge_count=0 -- nowhere near the deterministic cap (3) -- proves
    # this is the AI signal escalating early, not the ordinary budget.
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai", nudge_count=0)

    llm = QueueLLM([(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "concerning", "reason": "Hostile, disputing the invoice"})
    ]), 61)])
    settings = make_settings(CHASE_DRY_RUN=False, CHASE_SMART_ESCALATION_ENABLED=True)

    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings, llm)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "escalated"
    assert chase["total_tokens_used"] == 61
    events = await chase_store.list_events(chase_id)
    assert any(e["kind"] == "trajectory_assessed" and e["detail"]["verdict"] == "concerning" for e in events)
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_smart_escalation_progressing_verdict_defers_to_normal_nudge(
    backend, chase_store, project_store, messenger, email_sender
):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai", nudge_count=0)

    llm = QueueLLM([(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "progressing", "reason": "PM is actively engaging"})
    ]), 40)])
    settings = make_settings(CHASE_DRY_RUN=False, CHASE_SMART_ESCALATION_ENABLED=True)

    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings, llm)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "awaiting_pm"  # not escalated -- normal nudge happened instead
    assert chase["nudge_count"] == 1
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_smart_escalation_stalling_below_soft_threshold_does_not_escalate_early(
    backend, chase_store, project_store, messenger, email_sender
):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    # nudge_count=0, cap=3 -- well below the (cap - 1) soft threshold, so
    # "stalling" alone should NOT trigger early escalation here.
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai", nudge_count=0)

    llm = QueueLLM([(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "stalling", "reason": "Vague non-answer"})
    ]), 30)])
    settings = make_settings(CHASE_DRY_RUN=False, CHASE_SMART_ESCALATION_ENABLED=True, CHASE_MAX_NUDGES=3)

    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings, llm)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "awaiting_pm"
    assert chase["nudge_count"] == 1
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_smart_escalation_stalling_at_soft_threshold_escalates_early(
    backend, chase_store, project_store, messenger, email_sender
):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    # nudge_count=2, cap=3 -- exactly at (cap - 1), the soft threshold.
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai", nudge_count=2)

    llm = QueueLLM([(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "stalling", "reason": "Still vague after repeated asks"})
    ]), 30)])
    settings = make_settings(CHASE_DRY_RUN=False, CHASE_SMART_ESCALATION_ENABLED=True, CHASE_MAX_NUDGES=3)

    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings, llm)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "escalated"
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_smart_escalation_disabled_by_default_never_calls_the_llm(
    backend, chase_store, project_store, messenger, email_sender
):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    chase_id = await chase_store.create("case-1", next_action_at=past_iso())
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai", nudge_count=0)

    llm = QueueLLM([])  # would raise IndexError if ever called
    settings = make_settings(CHASE_DRY_RUN=False, CHASE_SMART_ESCALATION_ENABLED=False)

    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings, llm)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "awaiting_pm"
    assert chase["nudge_count"] == 1
    assert llm.calls == []
    await backend.close()
