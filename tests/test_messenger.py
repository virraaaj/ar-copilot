"""Phase 4 tests: TeamsMessenger swap logic + FakeMessenger recording,
plus the real BotFrameworkMessenger token-acquisition + Bot Connector call
shape (added 2026-07-17, respx-mocked -- never hits the real Bot Connector
API in tests)."""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.channels.teams.messenger import (
    BotFrameworkMessenger,
    ConversationServiceUrlUnknown,
    FakeMessenger,
    get_messenger,
)


@pytest.mark.asyncio
async def test_fake_messenger_records_text_and_cards():
    messenger = FakeMessenger()

    await messenger.send_text("conv-1", "hello")
    await messenger.send_card("conv-1", {"type": "AdaptiveCard"})

    assert len(messenger.sent) == 2
    assert messenger.sent[0].text == "hello"
    assert messenger.sent[1].card == {"type": "AdaptiveCard"}


@pytest.mark.asyncio
async def test_fake_messenger_create_group_conversation_records_members():
    messenger = FakeMessenger()

    conv_id = await messenger.create_group_conversation("Meridian Bay", ["pm@corehelix.ai", "finance@corehelix.ai"])

    assert conv_id in messenger.created_conversations
    assert messenger.created_conversations[conv_id] == ["pm@corehelix.ai", "finance@corehelix.ai"]


@pytest.mark.asyncio
async def test_fake_messenger_create_group_conversation_ids_are_unique():
    messenger = FakeMessenger()

    first = await messenger.create_group_conversation("Project A", ["a@corehelix.ai"])
    second = await messenger.create_group_conversation("Project B", ["b@corehelix.ai"])

    assert first != second


def test_get_messenger_defaults_to_fake_without_credentials(monkeypatch):
    monkeypatch.setenv("MICROSOFT_APP_ID", "")
    monkeypatch.setenv("MICROSOFT_APP_PASSWORD", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module
    import app.channels.teams.messenger as messenger_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    monkeypatch.setattr(messenger_module, "_messenger", None)

    result = get_messenger()

    assert isinstance(result, FakeMessenger)


def test_get_messenger_stays_fake_with_app_id_but_no_password(monkeypatch):
    """Locks in the real current state (2026-07-16): App ID + tenant ID were
    recovered from Lummus's CI config, but the secret was never committed
    anywhere -- only the App ID being set must NOT be enough to switch over."""
    monkeypatch.setenv("MICROSOFT_APP_ID", "815a093a-e8a9-436c-9bcf-f58959b23a9b")
    monkeypatch.setenv("MICROSOFT_APP_TENANT_ID", "21a36225-a922-460b-a044-4bf9bfe5d7fc")
    monkeypatch.setenv("MICROSOFT_APP_PASSWORD", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module
    import app.channels.teams.messenger as messenger_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    monkeypatch.setattr(messenger_module, "_messenger", None)

    result = get_messenger()

    assert isinstance(result, FakeMessenger)


def test_bot_framework_messenger_raises_clear_error_without_credentials(monkeypatch):
    monkeypatch.setenv("MICROSOFT_APP_ID", "")
    monkeypatch.setenv("MICROSOFT_APP_PASSWORD", "")
    monkeypatch.setenv("MICROSOFT_APP_TENANT_ID", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    with pytest.raises(RuntimeError, match="MICROSOFT_APP_ID"):
        BotFrameworkMessenger()


class _FakeServiceUrlStore:
    """Stands in for ProjectConversationStore's service_url methods --
    real DB behavior is already covered in test_project_conversation_store.py;
    this just needs to hand back a fixed answer."""

    def __init__(self, known: dict[str, str] | None = None):
        self.known = known or {}
        self.recorded: list[tuple[str, str]] = []

    async def get_service_url(self, conversation_id: str):
        return self.known.get(conversation_id)

    async def get_any_known_service_url(self):
        return next(iter(self.known.values()), None)

    async def record_service_url(self, conversation_id: str, service_url: str) -> None:
        self.recorded.append((conversation_id, service_url))
        self.known[conversation_id] = service_url


def _configured_bot_messenger(monkeypatch, store) -> BotFrameworkMessenger:
    monkeypatch.setenv("MICROSOFT_APP_ID", "test-app-id")
    monkeypatch.setenv("MICROSOFT_APP_PASSWORD", "test-app-password")
    monkeypatch.setenv("MICROSOFT_APP_TENANT_ID", "test-tenant-id")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    return BotFrameworkMessenger(service_url_store=store)


@pytest.mark.asyncio
async def test_send_text_raises_when_service_url_unknown(monkeypatch):
    messenger = _configured_bot_messenger(monkeypatch, _FakeServiceUrlStore())

    with pytest.raises(ConversationServiceUrlUnknown):
        await messenger.send_text("conv-1", "hello")


@pytest.mark.asyncio
async def test_create_group_conversation_raises_when_no_service_url_known(monkeypatch):
    messenger = _configured_bot_messenger(monkeypatch, _FakeServiceUrlStore())

    with pytest.raises(ConversationServiceUrlUnknown):
        await messenger.create_group_conversation("Topic", ["a@corehelix.ai"])


@pytest.mark.asyncio
@respx.mock
async def test_send_text_acquires_token_and_posts_activity(monkeypatch):
    store = _FakeServiceUrlStore({"conv-1": "https://smba.trafficmanager.net/amer/"})
    messenger = _configured_bot_messenger(monkeypatch, store)

    token_route = respx.post("https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token").mock(
        return_value=Response(200, json={"access_token": "fake-token", "expires_in": 3600})
    )
    activity_route = respx.post(
        "https://smba.trafficmanager.net/amer/v3/conversations/conv-1/activities"
    ).mock(return_value=Response(200, json={"id": "activity-1"}))

    await messenger.send_text("conv-1", "hello there")

    assert token_route.called
    assert activity_route.called
    import json

    body = json.loads(activity_route.calls[0].request.content)
    assert body == {"type": "message", "text": "hello there"}
    assert activity_route.calls[0].request.headers["Authorization"] == "Bearer fake-token"


@pytest.mark.asyncio
@respx.mock
async def test_send_card_posts_adaptive_card_attachment(monkeypatch):
    store = _FakeServiceUrlStore({"conv-1": "https://smba.trafficmanager.net/amer/"})
    messenger = _configured_bot_messenger(monkeypatch, store)

    respx.post("https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token").mock(
        return_value=Response(200, json={"access_token": "fake-token", "expires_in": 3600})
    )
    activity_route = respx.post(
        "https://smba.trafficmanager.net/amer/v3/conversations/conv-1/activities"
    ).mock(return_value=Response(200, json={"id": "activity-1"}))

    await messenger.send_card("conv-1", {"type": "AdaptiveCard", "body": []})

    import json

    body = json.loads(activity_route.calls[0].request.content)
    assert body["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert body["attachments"][0]["content"] == {"type": "AdaptiveCard", "body": []}


@pytest.mark.asyncio
@respx.mock
async def test_create_group_conversation_posts_to_bot_connector_and_records_service_url(monkeypatch):
    store = _FakeServiceUrlStore({"conv-existing": "https://smba.trafficmanager.net/amer/"})
    messenger = _configured_bot_messenger(monkeypatch, store)

    respx.post("https://login.microsoftonline.com/test-tenant-id/oauth2/v2.0/token").mock(
        return_value=Response(200, json={"access_token": "fake-token", "expires_in": 3600})
    )
    create_route = respx.post("https://smba.trafficmanager.net/amer/v3/conversations").mock(
        return_value=Response(201, json={"id": "conv-new-1"})
    )

    conversation_id = await messenger.create_group_conversation("Meridian Bay", ["pm@corehelix.ai"])

    assert conversation_id == "conv-new-1"
    assert create_route.called
    assert ("conv-new-1", "https://smba.trafficmanager.net/amer/") in store.recorded
