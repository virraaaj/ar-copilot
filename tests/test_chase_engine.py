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


# ---- advance_chase_with_reply ------------------------------------------------


def make_tool_call(name: str, arguments: dict):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class ScriptedLLM:
    def __init__(self, response, tokens=0):
        self._response = response
        self._tokens = tokens

    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
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
    assert updated["promised_by"] == "pm"

    events = await chase_store.list_events(chase_id)
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

    llm = QueueLLM([(SimpleNamespace(content="Hey! Just checking in on this one -- any update?"), 73)])
    settings = make_settings(CHASE_DRY_RUN=False, CHASE_COMPOSER_ENABLED=True)

    await process_due_chases(backend, messenger, email_sender, chase_store, project_store, settings, llm)

    assert email_sender.sent[0].html_body == "<p>Hey! Just checking in on this one -- any update?</p>"
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
