"""
Phase 4 tests: the proactive reminder poller, project-level model
(reworked 2026-07-16). respx-mocked backend, real SQLite dedup/
project-conversation-store against temp files, FakeMessenger.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.channels.teams.messenger import FakeMessenger
from app.channels.teams.proactive import ReminderDedup, send_due_reminders
from app.channels.teams.project_conversation_store import ProjectConversationStore
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
def store(tmp_path) -> ProjectConversationStore:
    return ProjectConversationStore(db_path=str(tmp_path / "state.db"))


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
async def test_sends_reminder_and_creates_project_conversation_on_first_use(backend, messenger, store, dedup):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )

    sent = await send_due_reminders(backend, messenger, store, dedup)

    assert sent == 1
    assert len(messenger.sent) == 1
    conversation_id = messenger.sent[0].conversation_id
    assert conversation_id in messenger.created_conversations
    assert messenger.created_conversations[conversation_id] == ["pm@corehelix.ai"]
    assert "Meridian Bay Terminal Expansion" in json.dumps(messenger.sent[0].card)
    # Now recorded for future passes/lookups (e.g. bot.py's chat-intent redirect).
    assert await store.get_conversation_id("PN-1") == conversation_id
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_reminder_card_buttons_are_openurl_magic_links(backend, messenger, store, dedup):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )

    await send_due_reminders(backend, messenger, store, dedup)

    card = messenger.sent[0].card
    actions = card["actions"]
    assert all(a["type"] == "Action.OpenUrl" for a in actions)
    assert {a["title"] for a in actions} == {"Snooze", "Add comment", "Follow up"}
    assert all("/link?token=" in a["url"] for a in actions)
    # Invoice-ID-free: the raw case id is never in the visible body, only in
    # the opaque signed token embedded in the button URLs.
    assert "case-1" not in json.dumps(card["body"])
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_second_pass_does_not_resend_same_case_and_stage(backend, messenger, store, dedup):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )

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
async def test_project_with_no_contacts_is_skipped_not_error(backend, messenger, store, dedup):
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
async def test_contacts_with_no_email_are_skipped_not_error(backend, messenger, store, dedup):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": None}]})
    )

    sent = await send_due_reminders(backend, messenger, store, dedup)

    assert sent == 0
    assert messenger.sent == []
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_multiple_cases_same_project_pool_into_one_conversation(backend, messenger, store, dedup):
    _mock_login()
    case_a = _case(case_id="case-a", project_number="PN-1")
    case_b = _case(case_id="case-b", project_number="PN-1", stage="escalation")
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [case_a, case_b]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )

    sent = await send_due_reminders(backend, messenger, store, dedup)

    assert sent == 2
    conversation_ids = {m.conversation_id for m in messenger.sent}
    assert len(conversation_ids) == 1  # both invoices pooled into the same project chat
    assert len(messenger.created_conversations) == 1  # only created once, not per case
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_multiple_projects_send_independently(backend, messenger, store, dedup):
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

    sent = await send_due_reminders(backend, messenger, store, dedup)

    assert sent == 2
    conversation_ids = {m.conversation_id for m in messenger.sent}
    assert len(conversation_ids) == 2
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_reuses_existing_project_conversation_instead_of_creating_a_new_one(backend, messenger, store, dedup):
    _mock_login()
    await store.record("PN-1", "conv-existing")
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(200, json={"items": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]})
    )

    sent = await send_due_reminders(backend, messenger, store, dedup)

    assert sent == 1
    assert messenger.sent[0].conversation_id == "conv-existing"
    assert messenger.created_conversations == {}  # never called create_group_conversation
    await backend.close()
