"""Tests: EmailSender swap logic + FakeEmailSender recording (added 2026-07-16),
plus the real GraphEmailSender token-acquisition + sendMail call shape
(added 2026-07-17, respx-mocked -- never hits the real Graph API in tests)."""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.services.email_sender import FakeEmailSender, GraphEmailSender, get_email_sender


@pytest.mark.asyncio
async def test_fake_email_sender_records_sends():
    sender = FakeEmailSender()

    result = await sender.send("customer@example.com", "Following up", "<p>Hi</p>", [("X-Dunning-Case-Id", "case-1")])

    assert result["success"] is True
    assert len(sender.sent) == 1
    assert sender.sent[0].to == "customer@example.com"
    assert sender.sent[0].subject == "Following up"
    assert ("X-Dunning-Case-Id", "case-1") in sender.sent[0].headers


def test_get_email_sender_defaults_to_fake_without_credentials(monkeypatch):
    monkeypatch.setenv("GRAPH_MAIL_TENANT_ID", "")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_ID", "")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_SECRET", "")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module
    import app.services.email_sender as email_sender_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    monkeypatch.setattr(email_sender_module, "_sender", None)

    result = get_email_sender()

    assert isinstance(result, FakeEmailSender)


def test_graph_email_sender_raises_clear_error_without_credentials(monkeypatch):
    monkeypatch.setenv("GRAPH_MAIL_TENANT_ID", "")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_ID", "")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_SECRET", "")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    with pytest.raises(RuntimeError, match="GRAPH_MAIL_TENANT_ID"):
        GraphEmailSender()


def _configured_graph_sender(monkeypatch) -> GraphEmailSender:
    monkeypatch.setenv("GRAPH_MAIL_TENANT_ID", "test-tenant-id")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "info@corehelix.ai")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    return GraphEmailSender()


@pytest.mark.asyncio
@respx.mock
async def test_graph_email_sender_send_acquires_token_and_posts_sendmail(monkeypatch):
    sender = _configured_graph_sender(monkeypatch)

    token_route = respx.post("https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token").mock(
        return_value=Response(200, json={"access_token": "fake-access-token", "expires_in": 3600})
    )
    send_route = respx.post("https://graph.microsoft.com/v1.0/users/info@corehelix.ai/sendMail").mock(
        return_value=Response(202)
    )

    result = await sender.send(
        "customer@example.com", "Following up", "<p>Hi</p>", [("X-Dunning-Case-Id", "case-1")]
    )

    assert result == {"success": True, "provider": "graph_api"}
    assert token_route.called
    assert send_route.called

    token_request = token_route.calls[0].request
    assert "grant_type=client_credentials" in token_request.content.decode()
    assert "client_secret=test-client-secret" in token_request.content.decode()

    send_body = send_route.calls[0].request
    assert send_body.headers["Authorization"] == "Bearer fake-access-token"
    import json

    payload = json.loads(send_body.content)
    assert payload["message"]["toRecipients"] == [{"emailAddress": {"address": "customer@example.com"}}]
    assert payload["message"]["subject"] == "Following up"
    assert payload["message"]["internetMessageHeaders"] == [{"name": "X-Dunning-Case-Id", "value": "case-1"}]
    assert payload["saveToSentItems"] is True


@pytest.mark.asyncio
@respx.mock
async def test_graph_email_sender_send_surfaces_graph_error(monkeypatch):
    sender = _configured_graph_sender(monkeypatch)

    respx.post("https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token").mock(
        return_value=Response(200, json={"access_token": "fake-access-token", "expires_in": 3600})
    )
    respx.post("https://graph.microsoft.com/v1.0/users/info@corehelix.ai/sendMail").mock(
        return_value=Response(403, text="Forbidden")
    )

    result = await sender.send("customer@example.com", "Following up", "<p>Hi</p>", [])

    assert result["success"] is False
    assert result["status_code"] == 403


@pytest.mark.asyncio
@respx.mock
async def test_graph_email_sender_reuses_cached_token_within_expiry(monkeypatch):
    sender = _configured_graph_sender(monkeypatch)

    token_route = respx.post("https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token").mock(
        return_value=Response(200, json={"access_token": "fake-access-token", "expires_in": 3600})
    )
    respx.post("https://graph.microsoft.com/v1.0/users/info@corehelix.ai/sendMail").mock(return_value=Response(202))

    await sender.send("a@example.com", "Subject 1", "<p>1</p>", [])
    await sender.send("b@example.com", "Subject 2", "<p>2</p>", [])

    assert token_route.call_count == 1
