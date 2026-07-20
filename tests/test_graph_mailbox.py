"""Tests: GraphMailboxReader (added 2026-07-20, Phase C3). respx-mocked,
same pattern as test_email_sender.py's GraphEmailSender tests -- never
hits the real Graph API."""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.services.graph_mailbox import GraphMailboxReader, get_mailbox_reader


def _configured_reader(monkeypatch) -> GraphMailboxReader:
    monkeypatch.setenv("GRAPH_MAIL_TENANT_ID", "test-tenant-id")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "info@corehelix.ai")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    return GraphMailboxReader()


def test_raises_clear_error_without_credentials(monkeypatch):
    monkeypatch.setenv("GRAPH_MAIL_TENANT_ID", "")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_ID", "")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_SECRET", "")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    with pytest.raises(RuntimeError, match="GRAPH_MAIL_TENANT_ID"):
        GraphMailboxReader()


def test_get_mailbox_reader_returns_none_without_credentials(monkeypatch):
    monkeypatch.setenv("GRAPH_MAIL_TENANT_ID", "")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_ID", "")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_SECRET", "")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module
    import app.services.graph_mailbox as graph_mailbox_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    monkeypatch.setattr(graph_mailbox_module, "_reader", None)

    assert get_mailbox_reader() is None


@pytest.mark.asyncio
@respx.mock
async def test_list_recent_messages_acquires_token_and_fetches(monkeypatch):
    reader = _configured_reader(monkeypatch)

    token_route = respx.post("https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token").mock(
        return_value=Response(200, json={"access_token": "fake-token", "expires_in": 3600})
    )
    list_route = respx.get(
        "https://graph.microsoft.com/v1.0/users/info@corehelix.ai/mailFolders/inbox/messages"
    ).mock(return_value=Response(200, json={"value": [{"id": "msg-1", "subject": "[AR-ABC123] Re: invoice"}]}))

    messages = await reader.list_recent_messages(top=10)

    assert token_route.called
    assert list_route.called
    assert messages == [{"id": "msg-1", "subject": "[AR-ABC123] Re: invoice"}]
    assert list_route.calls[0].request.headers["Authorization"] == "Bearer fake-token"


@pytest.mark.asyncio
@respx.mock
async def test_list_recent_messages_raises_on_graph_error(monkeypatch):
    reader = _configured_reader(monkeypatch)

    respx.post("https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token").mock(
        return_value=Response(200, json={"access_token": "fake-token", "expires_in": 3600})
    )
    respx.get("https://graph.microsoft.com/v1.0/users/info@corehelix.ai/mailFolders/inbox/messages").mock(
        return_value=Response(403, text="Forbidden")
    )

    with pytest.raises(RuntimeError, match="403"):
        await reader.list_recent_messages()
