"""Tests: manual follow-up campaign engine (added 2026-07-16)."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.channels.teams.messenger import FakeMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.services.backend_client import BackendClient
from app.services.chase_store import ChaseStore
from app.services.email_sender import FakeEmailSender
from app.services.followup_engine import send_due_followups, mirror_new_replies_to_teams
from app.services.followup_store import FollowUpStore

BASE = "http://test-backend"


def _mock_login():
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))


@pytest.fixture
def backend() -> BackendClient:
    return BackendClient(BASE, "svc@example.com", "password")


@pytest.fixture
def store(tmp_path) -> FollowUpStore:
    return FollowUpStore(db_path=str(tmp_path / "state.db"))


@pytest.fixture
def project_store(tmp_path) -> ProjectConversationStore:
    return ProjectConversationStore(db_path=str(tmp_path / "state.db"))


@pytest.fixture
def email_sender() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture
def messenger() -> FakeMessenger:
    return FakeMessenger()


def _case_json(case_id="case-1", project_number="PN-1", stage="first_notice"):
    return {
        "id": case_id,
        "case_key": "CK-1",
        "current_stage_code": stage,
        "project_number": project_number,
        "project_name": "Meridian Bay",
        "primary_invoice_id": "INV-1",
        "primary_invoice_open_amount": 50000,
    }


@pytest.mark.asyncio
@respx.mock
async def test_send_due_followups_sends_and_stamps_lineage(backend, email_sender, store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)

    sent = await send_due_followups(backend, email_sender, store)

    assert sent == 1
    assert len(email_sender.sent) == 1
    email = email_sender.sent[0]
    assert email.to == "customer@example.com"
    assert ("X-Dunning-Case-Id", "case-1") in email.headers
    assert "DUNNING-V2" in email.html_body
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_send_due_followups_records_local_send_history(backend, email_sender, store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)

    await send_due_followups(backend, email_sender, store)

    history = await store.list_send_history("case-1")
    assert len(history) == 1
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_send_due_followups_skips_not_yet_due_campaigns(backend, email_sender, store):
    _mock_login()
    cases_route = respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    campaign_id = await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)
    await store.mark_sent(campaign_id, "customer@example.com", cadence_days=3, end_date=None)  # pushes next_send_at 3 days out

    sent = await send_due_followups(backend, email_sender, store)

    assert sent == 0
    assert not cases_route.called
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_send_due_followups_skips_case_with_an_open_chase(backend, email_sender, store, tmp_path):
    _mock_login()
    cases_route = respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)
    chase_store = ChaseStore(db_path=str(tmp_path / "state.db"))
    await chase_store.create("case-1")

    sent = await send_due_followups(backend, email_sender, store, chase_store)

    assert sent == 0
    assert not cases_route.called
    assert email_sender.sent == []
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_mirror_new_replies_posts_to_project_conversation(backend, store, project_store, messenger):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1/timeline").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"event_type": "reply_received", "event_summary": "We'll pay Friday", "occurred_at": "2026-07-17T10:00:00"},
                    {"event_type": "case_created", "event_summary": "n/a", "occurred_at": "2026-07-14T10:00:00"},
                ]
            },
        )
    )
    await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)
    await project_store.record("PN-1", "conv-1")

    mirrored = await mirror_new_replies_to_teams(backend, store, project_store, messenger)

    assert mirrored == 1
    assert len(messenger.sent) == 1
    assert messenger.sent[0].conversation_id == "conv-1"
    assert "We'll pay Friday" in messenger.sent[0].text
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_mirror_new_replies_does_not_repeat_already_mirrored_replies(backend, store, project_store, messenger):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1/timeline").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"event_type": "reply_received", "event_summary": "We'll pay Friday", "occurred_at": "2026-07-17T10:00:00"}]},
        )
    )
    await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)
    await project_store.record("PN-1", "conv-1")

    first_pass = await mirror_new_replies_to_teams(backend, store, project_store, messenger)
    second_pass = await mirror_new_replies_to_teams(backend, store, project_store, messenger)

    assert first_pass == 1
    assert second_pass == 0
    assert len(messenger.sent) == 1
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_mirror_new_replies_skips_project_with_no_known_conversation(backend, store, project_store, messenger):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(return_value=httpx.Response(200, json=_case_json()))
    await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)
    # No project_store.record() call -- this project has no known Teams chat yet.

    mirrored = await mirror_new_replies_to_teams(backend, store, project_store, messenger)

    assert mirrored == 0
    assert messenger.sent == []
    await backend.close()
