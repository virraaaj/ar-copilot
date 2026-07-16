"""Tests: signed magic-link tokens for Teams -> web redirects (added 2026-07-16)."""
from __future__ import annotations

import time

import pytest

from app.guardrails.magic_link import (
    MagicLinkError,
    MagicLinkPayload,
    create_magic_link_token,
    verify_magic_link_token,
)


def test_round_trips_snooze_payload():
    token = create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="snooze", invoice_id="case-1"))

    payload = verify_magic_link_token(token)

    assert payload.email == "pm@corehelix.ai"
    assert payload.action == "snooze"
    assert payload.invoice_id == "case-1"
    assert payload.project_number is None


def test_round_trips_pick_invoice_payload():
    token = create_magic_link_token(
        MagicLinkPayload(email="pm@corehelix.ai", action="pick_invoice", project_number="PN-1", next_action="snooze")
    )

    payload = verify_magic_link_token(token)

    assert payload.action == "pick_invoice"
    assert payload.project_number == "PN-1"
    assert payload.next_action == "snooze"
    assert payload.invoice_id is None


def test_round_trips_follow_up_payload():
    token = create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="follow_up", invoice_id="case-1"))

    payload = verify_magic_link_token(token)

    assert payload.action == "follow_up"
    assert payload.invoice_id == "case-1"


def test_follow_up_requires_invoice_id():
    with pytest.raises(ValueError):
        create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="follow_up"))


def test_pick_invoice_accepts_follow_up_as_next_action():
    token = create_magic_link_token(
        MagicLinkPayload(email="pm@corehelix.ai", action="pick_invoice", project_number="PN-1", next_action="follow_up")
    )

    payload = verify_magic_link_token(token)

    assert payload.next_action == "follow_up"


def test_pick_invoice_requires_valid_next_action():
    with pytest.raises(ValueError):
        create_magic_link_token(
            MagicLinkPayload(email="pm@corehelix.ai", action="pick_invoice", project_number="PN-1", next_action="delete")
        )


def test_rejects_invalid_action():
    with pytest.raises(ValueError):
        create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="delete_everything", invoice_id="case-1"))


def test_snooze_requires_invoice_id():
    with pytest.raises(ValueError):
        create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="snooze"))


def test_pick_invoice_requires_project_number():
    with pytest.raises(ValueError):
        create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="pick_invoice"))


def test_tampered_token_is_rejected():
    token = create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="snooze", invoice_id="case-1"))
    body, sig = token.split(".", 1)
    tampered = body + "x." + sig  # corrupt the payload, keep the (now-mismatched) signature

    with pytest.raises(MagicLinkError):
        verify_magic_link_token(tampered)


def test_malformed_token_is_rejected():
    with pytest.raises(MagicLinkError):
        verify_magic_link_token("not-a-real-token")


def test_expired_token_is_rejected():
    token = create_magic_link_token(
        MagicLinkPayload(email="pm@corehelix.ai", action="snooze", invoice_id="case-1"), ttl_seconds=-1
    )

    with pytest.raises(MagicLinkError, match="expired"):
        verify_magic_link_token(token)


def test_different_secret_invalidates_signature(monkeypatch):
    token = create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="snooze", invoice_id="case-1"))

    import app.config as config_module
    s = config_module.get_settings()
    monkeypatch.setattr(s, "MAGIC_LINK_SECRET", "a-completely-different-secret")

    with pytest.raises(MagicLinkError):
        verify_magic_link_token(token)
