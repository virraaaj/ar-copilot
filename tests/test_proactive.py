"""
Phase 4 tests: the proactive reminder poller. respx-mocked backend, real
SQLite dedup/conversation-store against temp files, FakeMessenger.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.channels.teams.conversation_store import ConversationStore
from app.channels.teams.messenger import FakeMessenger
from app.channels.teams.proactive import ReminderDedup, send_due_reminders
from app.services.backend_client import BackendClient

BASE = "http://test-backend"


def _mock_login():
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))


@pytest.fixture
def backend() -> BackendClient:
    return BackendClient(BASE, "svc@example.com", "password")


@pytest.fixture
def messenger() -> FakeMessenger:
    return FakeMessenger()


@pytest.fixture
def store(tmp_path) -> ConversationStore:
    return ConversationStore(db_path=str(tmp_path / "state.db"))


@pytest.fixture
def dedup(tmp_path) -> ReminderDedup:
    return ReminderDedup(db_path=str(tmp_path / "state.db"))


def _case(case_id="case-1", stage="first_notice", project_number="PN-1"):
    return {
        "id": case_id,
        "case_key": "CK-1",
        "case_status": "active",
        "current_stage_code": stage,
        "project_number": project_number,
        "project_name": "Meridian Bay Terminal Expansion",
        "primary_invoice_open_amount": 1_250_000,
        "primary_invoice_aging_status": "31-60",
        "primary_invoice_due_date": "2026-06-21",
    }


@pytest.mark.asyncio
@respx.mock
async def test_sends_reminder_when_pm_known_and_stage_matches(backend, messenger, store, dedup):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    await store.record("pm@corehelix.ai", "conv-1")

    sent = await send_due_reminders(backend, messenger, store, dedup)

    assert sent == 1
    assert len(messenger.sent) == 1
    assert messenger.sent[0].conversation_id == "conv-1"
    assert "Meridian Bay Terminal Expansion" in json.dumps(messenger.sent[0].card)
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_second_pass_does_not_resend_same_case_and_stage(backend, messenger, store, dedup):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    await store.record("pm@corehelix.ai", "conv-1")

    first_pass = await send_due_reminders(backend, messenger, store, dedup)
    second_pass = await send_due_reminders(backend, messenger, store, dedup)

    assert first_pass == 1
    assert second_pass == 0
    assert len(messenger.sent) == 1  # still just the one from the first pass
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_non_outreach_stage_is_skipped(backend, messenger, store, dedup):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": [_case(stage="S0_pre_due")]})
    )

    sent = await send_due_reminders(backend, messenger, store, dedup)

    assert sent == 0
    assert messenger.sent == []
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_case_with_no_pm_contact_is_skipped_not_error(backend, messenger, store, dedup):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": []})  # no contacts at all
    )

    sent = await send_due_reminders(backend, messenger, store, dedup)  # must not raise

    assert sent == 0
    assert messenger.sent == []
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_pm_known_but_no_teams_conversation_yet_is_skipped(backend, messenger, store, dedup):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )
    # Note: no store.record() call -- this PM has never messaged the bot.

    sent = await send_due_reminders(backend, messenger, store, dedup)

    assert sent == 0
    assert messenger.sent == []
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_multiple_cases_send_independently(backend, messenger, store, dedup):
    _mock_login()
    case_a = _case(case_id="case-a", project_number="PN-A")
    case_b = _case(case_id="case-b", project_number="PN-B", stage="escalation")
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [case_a, case_b]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-A/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm-a@corehelix.ai"}]})
    )
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-B/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm-b@corehelix.ai"}]})
    )
    await store.record("pm-a@corehelix.ai", "conv-a")
    await store.record("pm-b@corehelix.ai", "conv-b")

    sent = await send_due_reminders(backend, messenger, store, dedup)

    assert sent == 2
    conversation_ids = {m.conversation_id for m in messenger.sent}
    assert conversation_ids == {"conv-a", "conv-b"}
    await backend.close()
