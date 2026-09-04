"""
Phase 0 tests: backend_client against a mocked API (respx), never a live call.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.services.backend_client import BackendClient, BackendError

BASE = "http://test-backend"


@pytest.fixture
def client() -> BackendClient:
    return BackendClient(BASE, "svc@example.com", "password")


@pytest.mark.asyncio
@respx.mock
async def test_login_then_list_cases(client: BackendClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": "fake-token"})
    )
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"id": "case-1", "current_stage_code": "reminder", "case_status": "active"}]},
        )
    )

    cases = await client.list_cases(limit=5)

    assert len(cases) == 1
    assert cases[0]["id"] == "case-1"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_login_failure_raises(client: BackendClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(401))

    with pytest.raises(BackendError, match="login failed"):
        await client.list_cases()
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_expired_token_retries_once(client: BackendClient) -> None:
    login_route = respx.post(f"{BASE}/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": "fake-token"})
    )
    cases_route = respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json={"items": []})]
    )

    cases = await client.list_cases()

    assert cases == []
    assert login_route.call_count == 2  # initial + re-login after 401
    assert cases_route.call_count == 2
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_pause_case_posts_reason(client: BackendClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": "fake-token"})
    )
    pause_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    result = await client.pause_case("case-1", reason="dispute", ends_at="2026-08-01")

    assert result == {"ok": True}
    sent_body = pause_route.calls.last.request.content
    assert b"dispute" in sent_body
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_pause_case_normalizes_bare_date_to_datetime(client: BackendClient) -> None:
    """Regression test for a real bug found live: the web form's
    <input type="date"> (and the agent tool schema, which says "ISO date")
    only ever produce a bare YYYY-MM-DD, but the real backend's Pydantic
    schema for ends_at requires a full datetime and 422s on a bare date
    ("invalid datetime separator, expected `T`..."). Every snooze with a
    resume date was broken until this was fixed."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    pause_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.pause_case("case-1", reason="dispute", ends_at="2026-08-01")

    sent_body = json.loads(pause_route.calls.last.request.content)
    assert sent_body["ends_at"] == "2026-08-01T00:00:00"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_pause_case_leaves_a_full_datetime_untouched(client: BackendClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    pause_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    await client.pause_case("case-1", reason="dispute", ends_at="2026-08-01T09:30:00")

    sent_body = json.loads(pause_route.calls.last.request.content)
    assert sent_body["ends_at"] == "2026-08-01T09:30:00"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_add_project_contact_rejects_bad_type(client: BackendClient) -> None:
    # Validation happens before any HTTP call, so no routes are mocked/expected.
    with pytest.raises(BackendError, match="contact_type must be one of"):
        await client.add_project_contact("PN-1", contact_type="ceo")
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_list_contacts_for_project_extracts_the_real_response_shape(client: BackendClient) -> None:
    """Regression test (found live 2026-07-20): this endpoint's real shape
    is {"project_number": ..., "contacts": [...]} -- NOT {"items": [...]}
    like list_all_project_contacts. Every caller (get_project_contacts
    chat tool, proactive.py's reminder lookup, chase_engine's PM-email
    resolution) was silently getting [] back from this call in production
    because it used the wrong unwrap helper, and every existing test had
    (wrongly) mocked the {"items": ...} shape too, so nothing caught it
    until a real live run against the actual UAT backend."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    respx.get(f"{BASE}/api/v1/dunning/projects/PN-1/contacts").mock(
        return_value=httpx.Response(
            200,
            json={"project_number": "PN-1", "contacts": [{"contact_type": "pm", "email": "pm@corehelix.ai"}]},
        )
    )

    contacts = await client.list_contacts_for_project("PN-1")

    assert contacts == [{"contact_type": "pm", "email": "pm@corehelix.ai"}]
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_list_default_project_contacts_returns_scopes(client: BackendClient) -> None:
    _mock_login = respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    respx.get(f"{BASE}/api/v1/dunning/default-project-contacts").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"bu": None, "bu_name": None, "contacts": [{"contact_id": "c-1", "contact_type": "pm", "name": "Jane", "email": "jane@x.com", "phone": None}]},
                {"bu": "201", "bu_name": "BU 201", "contacts": []},
            ],
        )
    )

    scopes = await client.list_default_project_contacts()

    assert scopes[0]["bu"] is None
    assert scopes[0]["contacts"][0]["contact_type"] == "pm"
    assert scopes[1]["bu"] == "201"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_add_default_project_contact_rejects_bad_type(client: BackendClient) -> None:
    with pytest.raises(BackendError, match="contact_type must be one of"):
        await client.add_default_project_contact(contact_type="ceo")
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_add_default_project_contact_posts_scope_and_fields(client: BackendClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    route = respx.post(f"{BASE}/api/v1/dunning/default-project-contacts").mock(
        return_value=httpx.Response(201, json={"contact_id": "c-1", "contact_type": "pm", "bu": "201"})
    )

    await client.add_default_project_contact(contact_type="pm", bu="201", name="Jane", email="jane@x.com")

    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body == {"contact_type": "pm", "bu": "201", "name": "Jane", "email": "jane@x.com", "phone": None}
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_delete_default_project_contact(client: BackendClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    route = respx.delete(f"{BASE}/api/v1/dunning/default-project-contacts/c-1").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    result = await client.delete_default_project_contact("c-1")

    assert result == {"ok": True}
    assert route.called
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_log_response_event_uses_real_schema_field_names(client: BackendClient) -> None:
    """Regression test: ResponseEventCreate (backend/app/dunning_v2/api/
    schemas.py) has no response_category or raw_text field -- an earlier
    version of this method sent those, and FastAPI/Pydantic silently drops
    unrecognized fields rather than rejecting the request, so the comment
    text was lost without any error ever surfacing. Confirmed live against
    the real UAT backend this session."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    route = respx.post(f"{BASE}/api/v2/dunning/response-events").mock(
        return_value=httpx.Response(200, json={"response_event_id": "re-1", "review_task_id": "rt-1", "case_id": "case-1"})
    )

    await client.log_response_event("case-1", raw_excerpt="Paying next week", source_channel="manual_only")

    sent_body = route.calls.last.request.content
    assert b"raw_excerpt" in sent_body
    assert b"response_category" not in sent_body
    assert b"raw_text" not in sent_body
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_list_aging_table_paginates_past_the_5000_row_server_cap(client: BackendClient) -> None:
    """Regression test, fixed 2026-09-04: the real endpoint caps its `limit`
    query param at 5000 (le=5000 server-side) and returns 200 either way, so
    a book bigger than one page used to truncate silently -- no exception,
    nothing to signal it. list_aging_table must now keep requesting
    successive `skip` pages until a short page comes back."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))

    page_size = 3  # small so the test doesn't need to build 5000+ rows
    all_rows = [{"invoice_number": f"INV-{i}", "open_amount": i} for i in range(7)]  # 3 + 3 + 1

    def responder(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        skip = int(params.get("skip", 0))
        limit = int(params.get("limit", page_size))
        page = all_rows[skip : skip + limit]
        return httpx.Response(200, json=page)

    route = respx.get(f"{BASE}/api/v1/dunning/aging-table").mock(side_effect=responder)

    rows = await client.list_aging_table(limit=page_size)

    assert rows == all_rows
    assert route.call_count == 3  # skip=0, skip=3, skip=6 (short page ends it)
    requested_skips = sorted(int(dict(c.request.url.params).get("skip", 0)) for c in route.calls)
    assert requested_skips == [0, 3, 6]
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_list_aging_table_single_short_page_makes_one_request(client: BackendClient) -> None:
    """The common case (a book that fits in one page) must not send a
    needless second request."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    rows_page = [{"invoice_number": "INV-1", "open_amount": 100}]
    route = respx.get(f"{BASE}/api/v1/dunning/aging-table").mock(return_value=httpx.Response(200, json=rows_page))

    rows = await client.list_aging_table()

    assert rows == rows_page
    assert route.call_count == 1
    await client.close()
