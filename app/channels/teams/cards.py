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
    case_key: str,
    project_name: str,
    stage_label: str,
    amount: str,
    aging: str,
    snooze_url: str,
    comment_url: str,
    follow_up_url: str,
    due_date: Optional[str] = None,
) -> Dict[str, Any]:
    """The proactive stage-triggered reminder (Phase 4's proactive.py sends
    this). Stage banner + Invoice details + Action needed callout +
    Snooze/Add comment/Follow up buttons.

    2026-07-16: buttons changed from Action.Submit (opened an in-Teams form
    card) to Action.OpenUrl, each carrying a signed one-time magic-link URL
    that lands the user already-authenticated on the matching web page —
    see guardrails/magic_link.py. Only `case_key` (the business-facing
    reference) ever appears in the visible body; the backend's internal case
    id lives only inside the opaque, signed token embedded in each URL —
    same invoice-ID-free principle as everywhere else, just carried by a
    token instead of hidden Action.Submit data now that the action happens
    on the web instead of in-Teams.

    2026-07-16 (later): added the Follow up button, for the "have the agent
    email the customer on a schedule" feature — same OpenUrl pattern, lands
    on the follow-up setup form (email, cadence, end date)."""
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
                        "text": "Please confirm receipt and a payment ETA with the customer, or use the buttons below.",
                        "wrap": True,
                    },
                ],
            },
            {"type": "TextBlock", "text": "Automated message · Lummus AR", "isSubtle": True, "size": "small"},
        ],
        actions=[
            {"type": "Action.OpenUrl", "title": "Snooze", "url": snooze_url},
            {"type": "Action.OpenUrl", "title": "Add comment", "url": comment_url},
            {"type": "Action.OpenUrl", "title": "Follow up", "url": follow_up_url},
        ],
    )


def redirect_card(message: str, url: str, button_title: str = "Open in AR Copilot") -> Dict[str, Any]:
    """Generic 'here's a link to finish this on the web' card — used by
    bot.py when free text in a project chat asks to snooze/comment (see the
    2026-07-16 redirect-to-web design). Carries a signed magic-link URL the
    same way reminder_card's buttons do."""
    return _card(
        body=[{"type": "TextBlock", "text": message, "wrap": True}],
        actions=[{"type": "Action.OpenUrl", "title": button_title, "url": url}],
    )


def error_card(message: str) -> Dict[str, Any]:
    return _card(body=[{"type": "TextBlock", "text": f"⚠ {message}", "wrap": True, "color": "attention"}])


def _format_amount(amount: float) -> str:
    return f"${amount:,.0f}"


def _trend_line(trend: Dict[str, Any]) -> str:
    amount_delta = trend["amount_delta"]
    if amount_delta > 0:
        arrow, verb = "▲", "up"
    elif amount_delta < 0:
        arrow, verb = "▼", "down"
    else:
        arrow, verb = "→", "unchanged"
    return f"{arrow} Open amount {verb} {_format_amount(abs(amount_delta))} vs last week"


def _projection_line(projection: Dict[str, Any]) -> str:
    customer_id = projection["customer_id"]
    if projection["sample_size"] == 0:
        return f"{customer_id}: not enough closed-invoice history yet for a projection."
    avg = projection["avg_days_relative_to_due"]
    if avg > 0:
        pattern = f"typically pays {avg:.0f} day{'s' if avg != 1 else ''} late"
    elif avg < 0:
        pattern = f"typically pays {abs(avg):.0f} day{'s' if avg != -1 else ''} early"
    else:
        pattern = "typically pays on time"
    return f"{customer_id}: {pattern} ({projection['risk']} risk, based on {projection['sample_size']} past invoice{'s' if projection['sample_size'] != 1 else ''})"


def digest_card(
    project_name: str,
    health: Dict[str, Any],
    trend: Optional[Dict[str, Any]],
    projections: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Weekly AR-health digest (added 2026-07-17, services/digest_engine.py
    sends this): open exposure + overdue count/amount for the project, the
    trend against last week's snapshot, and a payment-pattern projection
    per customer derived from that customer's own closed-case history.
    See digest_engine.py's module docstring for the projection methodology
    and its stated limitations (estimate, not a guarantee)."""
    body: List[Dict[str, Any]] = [
        {"type": "TextBlock", "text": "Weekly AR Digest", "weight": "bolder", "size": "medium"},
        {"type": "TextBlock", "text": project_name, "weight": "bolder", "wrap": True},
        _fact_set(
            {
                "Open invoices": str(health["open_invoice_count"]),
                "Total open": _format_amount(health["total_open_amount"]),
                "Overdue": f"{health['overdue_count']} ({_format_amount(health['overdue_amount'])})",
            }
        ),
    ]
    if trend:
        body.append({"type": "TextBlock", "text": _trend_line(trend), "isSubtle": True, "wrap": True, "spacing": "small"})

    if projections:
        body.append({"type": "TextBlock", "text": "Customer payment patterns", "weight": "bolder", "spacing": "medium"})
        for p in projections:
            body.append({"type": "TextBlock", "text": _projection_line(p), "wrap": True, "size": "small"})

    body.append({"type": "TextBlock", "text": "Automated message · Lummus AR", "isSubtle": True, "size": "small", "spacing": "medium"})
    return _card(body=body)


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
