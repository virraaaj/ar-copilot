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
async def test_list_policies_with_scope_filter(client: BackendClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    route = respx.get(f"{BASE}/api/v2/dunning/policies").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "pol-1", "scope_type": "global"}]})
    )

    result = await client.list_policies(scope_type="global")

    assert result == [{"id": "pol-1", "scope_type": "global"}]
    assert route.calls.last.request.url.params["scope_type"] == "global"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_update_stage_rule_patches_by_id(client: BackendClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    route = respx.patch(f"{BASE}/api/v2/dunning/stage-rules/rule-1").mock(
        return_value=httpx.Response(200, json={"id": "rule-1", "transition_rule_json": {"max_days_in_stage": 10}})
    )

    result = await client.update_stage_rule("rule-1", {"transition_rule_json": {"max_days_in_stage": 10}})

    assert result["transition_rule_json"]["max_days_in_stage"] == 10
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
