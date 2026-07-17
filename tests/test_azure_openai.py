"""Tests: AzureOpenAIService (added 2026-07-17, once a real credential
existed to test against for the first time this session)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.azure_openai import AzureOpenAIService


@pytest.fixture
def real_settings(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5-mini")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())


@pytest.mark.asyncio
async def test_chat_does_not_send_a_temperature_override(real_settings, monkeypatch):
    """Regression test for a real bug found live: gpt-5-mini (a
    reasoning-family model) rejects any non-default temperature with a 400
    ("Only the default (1) value is supported"), unlike GPT-4-class
    models. An earlier version hardcoded temperature=0.1 for every model,
    so real chat calls 400'd the moment a real credential existed to test
    against. Confirmed live against the real oai-sandbox-viraj-eastus2
    deployment."""
    service = AzureOpenAIService()

    fake_response = SimpleNamespace(choices=[SimpleNamespace(message="the message")])
    mock_create = AsyncMock(return_value=fake_response)
    service._client.chat.completions.create = mock_create

    await service.chat([{"role": "user", "content": "hi"}])

    sent_kwargs = mock_create.call_args.kwargs
    assert "temperature" not in sent_kwargs


@pytest.mark.asyncio
async def test_chat_passes_tools_when_given(real_settings):
    service = AzureOpenAIService()

    fake_response = SimpleNamespace(choices=[SimpleNamespace(message="the message")])
    mock_create = AsyncMock(return_value=fake_response)
    service._client.chat.completions.create = mock_create

    tools = [{"type": "function", "function": {"name": "list_invoices"}}]
    await service.chat([{"role": "user", "content": "hi"}], tools=tools, tool_choice="required")

    sent_kwargs = mock_create.call_args.kwargs
    assert sent_kwargs["tools"] == tools
    assert sent_kwargs["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_chat_omits_tools_when_none_given(real_settings):
    service = AzureOpenAIService()

    fake_response = SimpleNamespace(choices=[SimpleNamespace(message="the message")])
    mock_create = AsyncMock(return_value=fake_response)
    service._client.chat.completions.create = mock_create

    await service.chat([{"role": "user", "content": "hi"}])

    sent_kwargs = mock_create.call_args.kwargs
    assert "tools" not in sent_kwargs
    assert "tool_choice" not in sent_kwargs
