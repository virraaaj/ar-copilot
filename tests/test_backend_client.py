"""
Phase 0 tests: backend_client against a mocked API (respx), never a live call.
"""
from __future__ import annotations

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
async def test_add_project_contact_rejects_bad_type(client: BackendClient) -> None:
    # Validation happens before any HTTP call, so no routes are mocked/expected.
    with pytest.raises(BackendError, match="contact_type must be one of"):
        await client.add_project_contact("PN-1", contact_type="ceo")
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
