"""
Phase 4 tests: Adaptive Card builders. Pure dict construction, no network —
the invoice-ID-free assertions here are the ones that actually matter: the
internal invoice_id must never leak into visible card text.

2026-07-16: reminder_card's Snooze/Add-comment buttons became Action.OpenUrl
magic links (see channels/teams/cards.py); the in-Teams snooze/comment form
+ confirmation cards were removed since those actions now happen on the web.
"""
from __future__ import annotations

import json

from app.channels.teams.cards import (
    chase_escalation_card,
    digest_card,
    disambiguation_card,
    error_card,
    redirect_card,
    reminder_card,
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
        case_key="MRD-PRE-2601",
        project_name="Meridian Bay Terminal Expansion",
        stage_label="First Notice",
        amount="$1,250,000",
        aging="21 days",
        snooze_url="https://ar.example/link?token=snz-abc123internal",
        comment_url="https://ar.example/link?token=cmt-abc123internal",
        follow_up_url="https://ar.example/link?token=flw-abc123internal",
        due_date="2026-07-21",
    )

    visible = _visible_text(card)
    assert "MRD-PRE-2601" in visible
    assert "abc123internal" not in visible
    # ...but the URLs (with their opaque signed tokens) ARE in the action
    # data, since that's how the button actually gets the user there.
    assert "abc123internal" in json.dumps(card["actions"])


def test_reminder_card_has_snooze_comment_and_follow_up_openurl_actions():
    card = reminder_card(
        "CK-1", "Project X", "Escalation", "$500", "31-60 days",
        snooze_url="https://ar.example/link?token=snz-1",
        comment_url="https://ar.example/link?token=cmt-1",
        follow_up_url="https://ar.example/link?token=flw-1",
    )

    actions = card["actions"]
    assert all(a["type"] == "Action.OpenUrl" for a in actions)
    titles_to_urls = {a["title"]: a["url"] for a in actions}
    assert titles_to_urls["Snooze"] == "https://ar.example/link?token=snz-1"
    assert titles_to_urls["Add comment"] == "https://ar.example/link?token=cmt-1"
    assert titles_to_urls["Follow up"] == "https://ar.example/link?token=flw-1"


def test_redirect_card_carries_magic_link_url():
    card = redirect_card("Pick which invoice you'd like to snooze.", "https://ar.example/link?token=pick-1")

    assert "Pick which invoice" in _visible_text(card)
    assert card["actions"][0]["type"] == "Action.OpenUrl"
    assert card["actions"][0]["url"] == "https://ar.example/link?token=pick-1"


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


def test_digest_card_shows_project_and_health_facts():
    health = {"open_invoice_count": 5, "total_open_amount": 125000.0, "overdue_count": 2, "overdue_amount": 40000.0}
    card = digest_card("Meridian Bay", health, trend=None, projections=[])

    visible = _visible_text(card)
    assert "Meridian Bay" in visible
    assert "5" in visible
    assert "$125,000" in visible
    assert "$40,000" in visible


def test_digest_card_omits_trend_line_on_first_digest():
    health = {"open_invoice_count": 1, "total_open_amount": 1000.0, "overdue_count": 0, "overdue_amount": 0.0}
    card = digest_card("Project X", health, trend=None, projections=[])

    assert "vs last week" not in _visible_text(card)


def test_digest_card_shows_trend_direction():
    health = {"open_invoice_count": 1, "total_open_amount": 1000.0, "overdue_count": 0, "overdue_amount": 0.0}
    up = digest_card("Project X", health, trend={"amount_delta": 500.0, "count_delta": 1, "overdue_delta": 0}, projections=[])
    down = digest_card("Project X", health, trend={"amount_delta": -500.0, "count_delta": -1, "overdue_delta": 0}, projections=[])

    assert "up" in _visible_text(up).lower()
    assert "down" in _visible_text(down).lower()


def test_digest_card_shows_customer_projections():
    health = {"open_invoice_count": 1, "total_open_amount": 1000.0, "overdue_count": 0, "overdue_amount": 0.0}
    projections = [
        {"customer_id": "CUST-1", "sample_size": 3, "avg_days_relative_to_due": 18.0, "risk": "high"},
        {"customer_id": "CUST-2", "sample_size": 0, "avg_days_relative_to_due": None, "risk": "unknown"},
    ]
    card = digest_card("Project X", health, trend=None, projections=projections)

    visible = _visible_text(card)
    assert "CUST-1" in visible
    assert "18" in visible
    assert "late" in visible.lower()
    assert "high" in visible.lower()
    assert "CUST-2" in visible
    assert "not enough" in visible.lower()


def test_chase_escalation_card_shows_invoice_reason_and_review_link():
    card = chase_escalation_card(
        "INV-1", "No reply from pm after 3 nudges.", target="pm", missed_count=1,
        review_url="https://example.com/chases?chase_id=chase-1",
    )

    visible = _visible_text(card)
    assert "INV-1" in visible
    assert "No reply from pm after 3 nudges." in visible
    assert "the PM" in visible
    assert "1" in visible
    assert card["actions"][0]["type"] == "Action.OpenUrl"
    assert card["actions"][0]["url"] == "https://example.com/chases?chase_id=chase-1"


def test_chase_escalation_card_omits_optional_facts_when_absent():
    card = chase_escalation_card("INV-1", "Disputed.", target=None, missed_count=0, review_url="https://example.com")

    visible = _visible_text(card)
    assert "INV-1" in visible
    assert "Disputed." in visible


def test_to_attachment_wraps_card_in_bot_framework_envelope():
    card = error_card("test")

    attachment = to_attachment(card)

    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"] == card
