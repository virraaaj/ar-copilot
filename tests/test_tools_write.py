"""Phase 3 tests: write tools + policy enforcement, against a respx-mocked backend."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.agent.tools_write import add_comment, resume_invoice, snooze_invoice
from app.guardrails.policy import PolicyViolation
from app.services.backend_client import BackendClient

BASE = "http://test-backend"


@pytest.fixture
def client() -> BackendClient:
    return BackendClient(BASE, "svc@example.com", "password")


def _mock_login():
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))


@pytest.mark.asyncio
@respx.mock
async def test_snooze_invoice_happy_path(client: BackendClient) -> None:
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(
        return_value=httpx.Response(200, json={"id": "case-1", "current_stage_code": "first_notice"})
    )
    pause_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    result = await snooze_invoice(client, "case-1", reason="dispute", resume_date="2026-08-01")

    assert result["action"] == "snoozed"
    assert result["reason"] == "dispute"
    assert pause_route.called
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_snooze_invoice_refuses_pre_due(client: BackendClient) -> None:
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(
        return_value=httpx.Response(200, json={"id": "case-1", "current_stage_code": "S0_pre_due"})
    )
    pause_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause")

    with pytest.raises(PolicyViolation, match="Pre-Due"):
        await snooze_invoice(client, "case-1", reason="dispute")

    assert not pause_route.called  # refused before the backend was ever touched
    await client.close()


@pytest.mark.asyncio
async def test_snooze_invoice_requires_reason(client: BackendClient) -> None:
    with pytest.raises(PolicyViolation, match="reason"):
        await snooze_invoice(client, "case-1", reason="   ")
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_resume_invoice(client: BackendClient) -> None:
    _mock_login()
    resume_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/resume").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    result = await resume_invoice(client, "case-1")

    assert result["action"] == "resumed"
    assert resume_route.called
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_add_comment(client: BackendClient) -> None:
    _mock_login()
    comment_route = respx.post(f"{BASE}/api/v2/dunning/response-events").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    result = await add_comment(client, "case-1", comment="Customer confirmed payment next week", source_channel="teams")

    assert result["action"] == "commented"
    sent_body = json.loads(comment_route.calls.last.request.content)
    # Regression: must use the real ResponseEventCreate field names
    # (raw_excerpt), not the old raw_text/response_category, which the real
    # backend silently ignores rather than rejecting -- confirmed live
    # against the UAT backend this session, not just here.
    assert sent_body == {"case_id": "case-1", "source_channel": "teams", "raw_excerpt": "Customer confirmed payment next week"}
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_add_comment_defaults_to_manual_only_not_invalid_ar_copilot(client: BackendClient) -> None:
    """The old default source_channel="ar_copilot" isn't a valid StageChannel
    value on the real backend (email/voice_call/sms/manual_only/teams) --
    would have hard-failed with a 422 the moment anything used the default."""
    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))

    await add_comment(client, "case-1", comment="no explicit channel given")

    # add_comment's own default parameter, not a network assertion -- the
    # important thing is it's a real StageChannel value.
    import inspect
    assert inspect.signature(add_comment).parameters["source_channel"].default == "manual_only"
    await client.close()


@pytest.mark.asyncio
async def test_add_comment_requires_nonempty_text(client: BackendClient) -> None:
    with pytest.raises(PolicyViolation, match="comment"):
        await add_comment(client, "case-1", comment="")
    await client.close()
