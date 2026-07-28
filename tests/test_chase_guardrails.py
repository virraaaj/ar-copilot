"""Tests: chase_guardrails.py (added 2026-07-28, spec §6.15)."""
from __future__ import annotations

from datetime import date

from app.services.chase_guardrails import (
    BLACKOUT_DATES,
    check_blackout_date,
    check_message_language,
)


def test_blackout_date_allows_normal_days():
    result = check_blackout_date(date(2026, 7, 28))
    assert result.allowed is True


def test_blackout_date_blocks_configured_date(monkeypatch):
    monkeypatch.setattr("app.services.chase_guardrails.BLACKOUT_DATES", ["2026-12-25"])
    result = check_blackout_date(date(2026, 12, 25))
    assert result.allowed is False
    assert "blackout" in result.reason.lower()


def test_message_language_allows_normal_text():
    result = check_message_language("Following up on invoice INV-1, could you confirm a payment date?")
    assert result.allowed is True


def test_message_language_blocks_discount_offer():
    result = check_message_language("We could offer a 10% discount if you pay this week.")
    assert result.allowed is False
    assert "discount" in result.reason.lower()


def test_message_language_blocks_legal_threat():
    result = check_message_language("If this isn't paid we will pursue legal action.")
    assert result.allowed is False
    assert "legal action" in result.reason.lower()


def test_message_language_blocks_collections_agency_mention():
    result = check_message_language("This will be sent to a collections agency.")
    assert result.allowed is False
