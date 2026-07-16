"""Phase 4 tests: TeamsMessenger swap logic + FakeMessenger recording."""
from __future__ import annotations

import pytest

from app.channels.teams.messenger import BotFrameworkMessenger, FakeMessenger, get_messenger


@pytest.mark.asyncio
async def test_fake_messenger_records_text_and_cards():
    messenger = FakeMessenger()

    await messenger.send_text("conv-1", "hello")
    await messenger.send_card("conv-1", {"type": "AdaptiveCard"})

    assert len(messenger.sent) == 2
    assert messenger.sent[0].text == "hello"
    assert messenger.sent[1].card == {"type": "AdaptiveCard"}


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
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    with pytest.raises(RuntimeError, match="MICROSOFT_APP_ID"):
        BotFrameworkMessenger()
