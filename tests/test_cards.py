"""
Phase 4 tests: Adaptive Card builders. Pure dict construction, no network —
but the invoice-ID-free assertions here are the ones that actually matter:
the internal invoice_id must never leak into visible card text, only into
each button's hidden Action.Submit data.
"""
from __future__ import annotations

import json

from app.channels.teams.cards import (
    comment_confirmation_card,
    comment_form_card,
    disambiguation_card,
    error_card,
    reminder_card,
    snooze_confirmation_card,
    snooze_form_card,
    to_attachment,
)


def _visible_text(card: dict) -> str:
    """Flatten every TextBlock/FactSet value in a card's body -- what a
    human would actually see, excluding hidden action `data` payloads."""
    chunks = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "TextBlock" and "text" in node:
                chunks.append(node["text"])
            if node.get("type") == "FactSet":
                for f in node.get("facts", []):
                    chunks.append(f.get("title", ""))
                    chunks.append(f.get("value", ""))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(card.get("body", []))
    # Action *titles* are visible (button labels); action *data* is not.
    for action in card.get("actions", []):
        chunks.append(action.get("title", ""))
    return " ".join(chunks)


def _hidden_data(card: dict) -> str:
    return json.dumps([a.get("data", {}) for a in card.get("actions", [])])


def test_reminder_card_shows_case_key_not_internal_invoice_id():
    card = reminder_card(
        invoice_id="a1b2c3d4-internal-uuid",
        case_key="MRD-PRE-2601",
        project_name="Meridian Bay Terminal Expansion",
        stage_label="First Notice",
        amount="$1,250,000",
        aging="21 days",
        due_date="2026-07-21",
    )

    visible = _visible_text(card)
    assert "MRD-PRE-2601" in visible
    assert "a1b2c3d4-internal-uuid" not in visible
    # ...but the internal id IS present in the hidden action data, since the
    # tool handlers need it to actually operate.
    assert "a1b2c3d4-internal-uuid" in _hidden_data(card)


def test_reminder_card_has_snooze_and_comment_actions():
    card = reminder_card("id-1", "CK-1", "Project X", "Escalation", "$500", "31-60 days")

    titles = [a["title"] for a in card["actions"]]
    assert "Snooze" in titles
    assert "Add comment" in titles


def test_snooze_form_card_carries_invoice_id_hidden():
    card = snooze_form_card("id-1", "Project X -- $500, 31-60 days")

    assert "id-1" not in _visible_text(card)
    assert "id-1" in _hidden_data(card)
    field_ids = {item.get("id") for item in card["body"] if item.get("type", "").startswith("Input")}
    assert field_ids == {"reason", "resume_date"}


def test_comment_form_card_carries_invoice_id_hidden():
    card = comment_form_card("id-1", "Project X")

    assert "id-1" not in _visible_text(card)
    assert "id-1" in _hidden_data(card)
    field_ids = {item.get("id") for item in card["body"] if item.get("type", "").startswith("Input")}
    assert field_ids == {"comment"}


def test_snooze_confirmation_card_shows_reason_and_date():
    card = snooze_confirmation_card("Project X", reason="dispute", resume_date="2026-08-01")

    visible = _visible_text(card)
    assert "dispute" in visible
    assert "2026-08-01" in visible


def test_snooze_confirmation_card_handles_no_resume_date():
    card = snooze_confirmation_card("Project X", reason="dispute", resume_date=None)

    assert "manually" in _visible_text(card).lower()


def test_comment_confirmation_card_quotes_the_comment():
    card = comment_confirmation_card("Project X", "Customer confirmed payment next week")

    assert "Customer confirmed payment next week" in _visible_text(card)


def test_error_card_shows_message():
    card = error_card("The Pre-Due stage cannot be snoozed.")

    assert "cannot be snoozed" in _visible_text(card)


def test_disambiguation_card_shows_labels_not_ids():
    candidates = [
        {"invoice_id": "id-1", "label": "Meridian Bay -- $1.25M, 21 days overdue"},
        {"invoice_id": "id-2", "label": "Falcon Ridge -- $500K, 5 days overdue"},
    ]
    card = disambiguation_card("Which invoice did you mean?", candidates)

    visible = _visible_text(card)
    assert "Meridian Bay" in visible
    assert "Falcon Ridge" in visible
    assert "id-1" not in visible
    assert "id-2" not in visible
    assert "id-1" in _hidden_data(card) and "id-2" in _hidden_data(card)


def test_to_attachment_wraps_card_in_bot_framework_envelope():
    card = error_card("test")

    attachment = to_attachment(card)

    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"] == card
