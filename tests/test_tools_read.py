"""Tests: read-only tools over backend_client, respx-mocked."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.agent.tools_read import get_chase_status, get_timeline, list_invoices
from app.services.backend_client import BackendClient
from app.services.chase_store import ChaseStore

BASE = "http://test-backend"


def _mock_login():
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))


@pytest.fixture
def client() -> BackendClient:
    return BackendClient(BASE, "svc@example.com", "password")


@pytest.fixture
def chase_state_db(tmp_path, monkeypatch):
    """get_chase_status builds its own ChaseStore() internally (same
    pattern as start_follow_up's FollowUpStore()), reading STATE_DB_PATH
    from settings -- point that at a temp file so tests are isolated."""
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    return str(tmp_path / "state.db")


@pytest.mark.asyncio
@respx.mock
async def test_get_timeline_uses_real_occurred_at_field_name(client: BackendClient) -> None:
    """Regression test: CaseTimelineEventResponse's timestamp field is
    occurred_at, not created_at/at -- an earlier version checked the wrong
    names, so every non-comment event silently rendered with no timestamp.
    Confirmed against live UAT data."""
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1/timeline").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "event_type": "email_sent",
                        "event_title": "Action sent",
                        "event_summary": "Sent to 2/2 recipients",
                        "actor_type": "system",
                        "occurred_at": "2026-07-14T12:00:00",
                    }
                ]
            },
        )
    )

    events = await get_timeline(client, invoice_id="case-1")

    assert events[0]["at"] == "2026-07-14T12:00:00"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_get_timeline_handles_missing_title_and_summary(client: BackendClient) -> None:
    """Some event types (e.g. bucket_floor_promotion_multi) have no
    event_title/event_summary on the real backend -- must not error."""
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1/timeline").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"event_type": "bucket_floor_promotion_multi", "occurred_at": "2026-07-14T12:00:00"}]},
        )
    )

    events = await get_timeline(client, invoice_id="case-1")

    assert events[0]["title"] is None
    assert events[0]["summary"] is None
    assert events[0]["at"] == "2026-07-14T12:00:00"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_list_invoices_surfaces_the_real_invoice_number(client: BackendClient) -> None:
    """The real human-facing invoice number lives in primary_invoice_id
    (e.g. "UAT-RND-FIN-002"), distinct from case_key (an internal
    engine-generated reference like "V2-AUTO-UAT-RND-FIN-002") -- verified
    against live UAT data. Added so the Dashboard can label/group invoices
    by their actual invoice number instead of repeating the project name."""
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "case-1", "case_key": "V2-AUTO-RND-002", "primary_invoice_id": "UAT-RND-002"}]},
        )
    )

    invoices = await list_invoices(client)

    assert invoices[0]["invoice_no"] == "UAT-RND-002"
    assert invoices[0]["case_key"] == "V2-AUTO-RND-002"
    await client.close()


@pytest.mark.asyncio
async def test_get_chase_status_no_chase_yet(client: BackendClient, chase_state_db) -> None:
    result = await get_chase_status(client, invoice_id="case-unknown")

    assert result == {"invoice_id": "case-unknown", "has_chase": False}


@pytest.mark.asyncio
async def test_get_chase_status_returns_current_state(client: BackendClient, chase_state_db) -> None:
    store = ChaseStore(db_path=chase_state_db)
    chase_id = await store.create("case-1", invoice_no="INV-1")
    await store.update(chase_id, state="commitment_tracked", target="pm", promised_date="2026-08-01",
                        promised_by="pm", missed_count=1, nudge_count=2)

    result = await get_chase_status(client, invoice_id="case-1")

    assert result["has_chase"] is True
    assert result["state"] == "commitment_tracked"
    assert result["promised_date"] == "2026-08-01"
    assert result["promised_by"] == "pm"
    assert result["missed_count"] == 1
    assert result["nudge_count"] == 2
