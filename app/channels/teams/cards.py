"""
Adaptive Card builders (PLAN.md §5 Phase 4). Copy and fields mirror the
proven cards from the earlier bot — verified against real screenshots taken
during this session (a reminder card with Invoice details/Action needed/
Snooze+Add comment, a "Snooze a follow-up" form, an "Add a note" form, and
their confirmation cards), not reinvented from scratch.

Pure functions returning dicts (Adaptive Card 1.4 schema) — no Bot Framework
SDK dependency here. `to_attachment()` wraps a card in the envelope shape a
Bot Framework Activity expects, applied at the point of sending.

Invoice-ID-free resolution (PLAN.md's design principle) applies here too:
every button's Action.Submit carries invoice_id in `data` (hidden from the
user), while the visible card text only ever shows human-readable fields.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

ADAPTIVE_CARD_VERSION = "1.4"
ADAPTIVE_CARD_TYPE = "AdaptiveCard"
ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"


def to_attachment(card: Dict[str, Any]) -> Dict[str, Any]:
    """The envelope a Bot Framework Activity.attachments entry expects."""
    return {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}


def _card(body: List[Dict[str, Any]], actions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    card: Dict[str, Any] = {
        "type": ADAPTIVE_CARD_TYPE,
        "$schema": ADAPTIVE_CARD_SCHEMA,
        "version": ADAPTIVE_CARD_VERSION,
        "body": body,
    }
    if actions:
        card["actions"] = actions
    return card


def _fact_set(facts: Dict[str, str]) -> Dict[str, Any]:
    return {"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in facts.items()]}


def reminder_card(
    invoice_id: str,
    case_key: str,
    project_name: str,
    stage_label: str,
    amount: str,
    aging: str,
    due_date: Optional[str] = None,
) -> Dict[str, Any]:
    """The proactive stage-triggered reminder (Phase 4's proactive.py sends
    this). Stage banner + Invoice details + Action needed callout +
    Snooze/Add comment buttons — matches the earlier bot's card exactly.

    `invoice_id` (the backend's internal case id) never appears in the
    visible body — only `case_key` (the business-facing reference) does.
    `invoice_id` rides in the button's hidden Action.Submit data, same
    invoice-ID-free principle as the web UI."""
    facts = {"Invoice": case_key, "Amount": amount, "Aging": aging}
    if due_date:
        facts["Due date"] = due_date

    return _card(
        body=[
            {
                "type": "Container",
                "style": "attention",
                "bleed": True,
                "items": [
                    {"type": "TextBlock", "text": f"⚠ {stage_label}", "weight": "bolder", "size": "medium", "color": "attention"},
                    {"type": "TextBlock", "text": project_name, "weight": "bolder", "wrap": True},
                ],
            },
            {"type": "TextBlock", "text": "Invoice details", "weight": "bolder", "spacing": "medium"},
            _fact_set(facts),
            {
                "type": "Container",
                "style": "warning",
                "items": [
                    {"type": "TextBlock", "text": "⚡ Action needed", "weight": "bolder"},
                    {
                        "type": "TextBlock",
                        "text": "Please confirm receipt and a payment ETA with the customer, or use the buttons below to snooze or add a note.",
                        "wrap": True,
                    },
                ],
            },
            {"type": "TextBlock", "text": "Automated message · Lummus AR", "isSubtle": True, "size": "small"},
        ],
        actions=[
            {"type": "Action.Submit", "title": "Snooze", "data": {"action": "open_snooze_form", "invoice_id": invoice_id, "label": project_name}},
            {"type": "Action.Submit", "title": "Add comment", "data": {"action": "open_comment_form", "invoice_id": invoice_id, "label": project_name}},
        ],
    )


def snooze_form_card(invoice_id: str, label: str) -> Dict[str, Any]:
    """'Snooze a follow-up' — opened by tapping Snooze on a reminder card,
    or by the agent when a chat message asks to snooze without giving a
    reason/date yet."""
    return _card(
        body=[
            {"type": "TextBlock", "text": "⏸ Snooze a follow-up", "weight": "bolder", "size": "medium"},
            {"type": "TextBlock", "text": label, "isSubtle": True, "wrap": True},
            {"type": "Input.Text", "id": "reason", "label": "Reason", "isRequired": True, "errorMessage": "A reason is required."},
            {"type": "Input.Date", "id": "resume_date", "label": "Resume on (optional)"},
        ],
        actions=[
            {"type": "Action.Submit", "title": "Confirm snooze", "data": {"action": "snooze_submit", "invoice_id": invoice_id, "label": label}},
        ],
    )


def comment_form_card(invoice_id: str, label: str) -> Dict[str, Any]:
    """'Add a note' — opened by tapping Add comment on a reminder card."""
    return _card(
        body=[
            {"type": "TextBlock", "text": "📝 Add a note", "weight": "bolder", "size": "medium"},
            {"type": "TextBlock", "text": label, "isSubtle": True, "wrap": True},
            {"type": "Input.Text", "id": "comment", "label": "Comment", "isRequired": True, "isMultiline": True, "errorMessage": "A comment is required."},
        ],
        actions=[
            {"type": "Action.Submit", "title": "Save comment", "data": {"action": "comment_submit", "invoice_id": invoice_id, "label": label}},
        ],
    )


def snooze_confirmation_card(label: str, reason: str, resume_date: Optional[str]) -> Dict[str, Any]:
    body = [
        {"type": "TextBlock", "text": "✅ Snoozed", "weight": "bolder", "color": "good"},
        {"type": "TextBlock", "text": label, "wrap": True},
        _fact_set({"Reason": reason, "Resume on": resume_date or "Not set -- resume manually"}),
    ]
    return _card(body=body)


def comment_confirmation_card(label: str, comment: str) -> Dict[str, Any]:
    return _card(
        body=[
            {"type": "TextBlock", "text": "✅ Comment saved", "weight": "bolder", "color": "good"},
            {"type": "TextBlock", "text": label, "wrap": True},
            {"type": "TextBlock", "text": f"“{comment}”", "wrap": True, "isSubtle": True},
        ]
    )


def error_card(message: str) -> Dict[str, Any]:
    return _card(body=[{"type": "TextBlock", "text": f"⚠ {message}", "wrap": True, "color": "attention"}])


def disambiguation_card(question: str, candidates: List[Dict[str, str]]) -> Dict[str, Any]:
    """Phase 1's invoice-ID-free resolution rule, in Teams form: one
    tappable option per match, human-readable label only. Each candidate is
    {"invoice_id": ..., "label": "Meridian Bay -- $1.25M, 21 days overdue"}."""
    return _card(
        body=[{"type": "TextBlock", "text": question, "wrap": True}],
        actions=[
            {"type": "Action.Submit", "title": c["label"], "data": {"action": "disambiguate_select", "invoice_id": c["invoice_id"], "label": c["label"]}}
            for c in candidates
        ],
    )
