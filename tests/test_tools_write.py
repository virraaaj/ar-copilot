"""Phase 3 tests: write tools + policy enforcement, against a respx-mocked backend."""
from __future__ import annotations

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
    sent_body = comment_route.calls.last.request.content
    assert b"teams" in sent_body
    await client.close()


@pytest.mark.asyncio
async def test_add_comment_requires_nonempty_text(client: BackendClient) -> None:
    with pytest.raises(PolicyViolation, match="comment"):
        await add_comment(client, "case-1", comment="")
    await client.close()
