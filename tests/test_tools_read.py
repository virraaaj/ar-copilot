"""Tests: read-only tools over backend_client, respx-mocked."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.agent.tools_read import get_chase_status, get_invoice, get_timeline, list_invoices, list_projects
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
    respx.get(f"{BASE}/api/v1/dunning/invoices").mock(
        return_value=httpx.Response(200, json=[{"id": "UAT-RND-002", "invoice_no": "UAT-RND-002", "status": "Open"}])
    )
    respx.get(f"{BASE}/api/v1/dunning/project-contacts").mock(return_value=httpx.Response(200, json=[]))

    invoices = await list_invoices(client)

    assert invoices[0]["invoice_no"] == "UAT-RND-002"
    assert invoices[0]["case_key"] == "V2-AUTO-RND-002"
    await client.close()


# ---------------------------------------------------------------------------
# case-independent invoice listing (added 2026-07-24) -- an uploaded invoice
# that isn't overdue yet has no dunning case (the case-feeder only creates
# cases for genuinely past-due invoices, by design), so it never showed up
# anywhere in the app -- not the Dashboard, not the project picker, nothing
# -- even though the invoice itself synced fine. list_invoices/list_projects/
# get_invoice/get_timeline all needed a case-independent path.


@pytest.mark.asyncio
@respx.mock
async def test_list_invoices_includes_invoices_with_no_case_yet(client: BackendClient) -> None:
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "case-1", "primary_invoice_id": "OVERDUE-1", "project_number": "PN-1", "project_name": "Overdue Project"}]},
        )
    )
    respx.get(f"{BASE}/api/v1/dunning/invoices").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "OVERDUE-1", "invoice_no": "OVERDUE-1", "status": "Open", "project_number": "PN-1", "due_date": "2026-07-01", "amount": 1000},
                {"id": "PREDUE-1", "invoice_no": "PREDUE-1", "status": "Open", "project_number": "PN-2", "due_date": "2026-08-15", "amount": 2000},
            ],
        )
    )
    respx.get(f"{BASE}/api/v1/dunning/project-contacts").mock(
        return_value=httpx.Response(200, json=[{"project_number": "PN-2", "project_name": "Pre-Due Project"}])
    )

    invoices = await list_invoices(client)

    by_no = {i["invoice_no"]: i for i in invoices}
    assert set(by_no) == {"OVERDUE-1", "PREDUE-1"}
    # the cased one keeps its real case id/project_name from the case
    assert by_no["OVERDUE-1"]["invoice_id"] == "case-1"
    assert by_no["OVERDUE-1"]["project_name"] == "Overdue Project"
    # the case-less one gets its own raw id and project_name from contacts
    assert by_no["PREDUE-1"]["invoice_id"] == "PREDUE-1"
    assert by_no["PREDUE-1"]["project_name"] == "Pre-Due Project"
    assert by_no["PREDUE-1"]["open_amount"] == 2000
    assert by_no["PREDUE-1"]["case_key"] is None
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_list_projects_includes_a_project_with_no_cased_invoices(client: BackendClient) -> None:
    _mock_login()
    respx.get(f"{BASE}/api/v1/dunning/project-contacts").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"project_number": "PN-1", "project_name": "Has Cases"},
                {"project_number": "PN-2", "project_name": "No Cases Yet"},
            ],
        )
    )

    projects = await list_projects(client)

    assert {p["project_number"] for p in projects} == {"PN-1", "PN-2"}
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_get_invoice_falls_back_to_raw_invoice_when_no_case_exists(client: BackendClient) -> None:
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/PREDUE-1").mock(return_value=httpx.Response(404, json={"detail": "not found"}))
    respx.get(f"{BASE}/api/v1/dunning/invoices/PREDUE-1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "PREDUE-1", "invoice_no": "PREDUE-1", "status": "Open", "project_number": "PN-2", "due_date": "2026-08-15", "amount": 2000},
        )
    )
    respx.get(f"{BASE}/api/v1/dunning/project-contacts").mock(
        return_value=httpx.Response(200, json=[{"project_number": "PN-2", "project_name": "Pre-Due Project"}])
    )

    invoice = await get_invoice(client, invoice_id="PREDUE-1")

    assert invoice["invoice_id"] == "PREDUE-1"
    assert invoice["project_name"] == "Pre-Due Project"
    assert invoice["open_amount"] == 2000
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_get_timeline_returns_empty_for_a_case_less_invoice(client: BackendClient) -> None:
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/PREDUE-1/timeline").mock(return_value=httpx.Response(404, json={"detail": "not found"}))

    events = await get_timeline(client, invoice_id="PREDUE-1")

    assert events == []
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
