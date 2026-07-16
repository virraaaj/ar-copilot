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


def test_bot_framework_messenger_raises_clear_error_without_credentials(monkeypatch):
    monkeypatch.setenv("MICROSOFT_APP_ID", "")
    monkeypatch.setenv("MICROSOFT_APP_PASSWORD", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    with pytest.raises(RuntimeError, match="MICROSOFT_APP_ID"):
        BotFrameworkMessenger()
