"""Tests: EmailSender swap logic + FakeEmailSender recording (added 2026-07-16)."""
from __future__ import annotations

import pytest

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
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_ID", "")
    monkeypatch.setenv("GRAPH_MAIL_CLIENT_SECRET", "")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    with pytest.raises(RuntimeError, match="GRAPH_MAIL_CLIENT_ID"):
        GraphEmailSender()
