"""
Write tools (PLAN.md §5 Phase 3): snooze_invoice, resume_invoice,
add_comment, start_follow_up.

Deliberately small blast radius (guardrails §6.6): each operates on exactly
one invoice per call, and every write requires non-empty text (a reason for
snooze/comment) — enforced by policy.py before the backend is ever called.
Registered in setup.py with extra_roles={"pm"}, so a PM can use these (via
Teams cards, the web forms, or conversationally through the agent) without
getting the rest of the tool surface.

Known gap (added 2026-07-16 alongside start_follow_up, not fixed here):
the direct web endpoints (app/channels/web.py) know which real user is
acting and tag the write accordingly (a comment's "[email] " prefix, a
campaign's requested_by), but a write made through this agent tool path
doesn't get that same identity threaded in — AgentLoop calls
`handler(backend, **args)` with no caller-identity parameter. Comments/
campaigns started via chat are attributed generically rather than to the
specific user, until that's worth threading through.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.guardrails.policy import (
    PolicyViolation,
    check_future_or_today,
    check_nonempty_text,
    check_positive_int,
    check_snooze_allowed,
    check_valid_email,
)
from app.services.backend_client import BackendClient
from app.services.followup_store import FollowUpError, FollowUpStore


async def snooze_invoice(
    client: BackendClient,
    invoice_id: str,
    reason: str,
    resume_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Pause dunning on one invoice. Refuses Pre-Due (nothing to pause yet)
    and requires a reason."""
    check_nonempty_text(reason, "reason")
    case = await client.get_case(invoice_id)
    check_snooze_allowed(case.get("current_stage_code"))
    result = await client.pause_case(invoice_id, reason=reason, ends_at=resume_date)
    return {"invoice_id": invoice_id, "action": "snoozed", "reason": reason, "resume_date": resume_date, "result": result}


async def resume_invoice(client: BackendClient, invoice_id: str) -> Dict[str, Any]:
    """Resume dunning on a previously-snoozed invoice."""
    result = await client.resume_case(invoice_id)
    return {"invoice_id": invoice_id, "action": "resumed", "result": result}


async def add_comment(
    client: BackendClient,
    invoice_id: str,
    comment: str,
    source_channel: str = "manual_only",
) -> Dict[str, Any]:
    """Log a comment against an invoice. source_channel must be one of the
    backend's real StageChannel values (email/voice_call/sms/manual_only/
    teams) -- "manual_only" is the fit for a comment entered through the web
    UI or agent chat; the Teams channel (bot.py) passes "teams" explicitly."""
    check_nonempty_text(comment, "comment")
    result = await client.log_response_event(invoice_id, raw_excerpt=comment, source_channel=source_channel)
    return {"invoice_id": invoice_id, "action": "commented", "comment": comment, "result": result}


async def start_follow_up(
    client: BackendClient,
    invoice_id: str,
    customer_email: str,
    cadence_days: int,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Starts a recurring follow-up email campaign for one invoice: sends
    now, then repeats every cadence_days until cancelled or end_date
    passes. See services/followup_engine.py for how sends actually go out.
    Refuses a second campaign while one's already active for this invoice."""
    check_valid_email(customer_email)
    check_positive_int(cadence_days, "cadence_days")
    check_future_or_today(end_date, "end_date")

    store = FollowUpStore()
    try:
        campaign_id = await store.create(
            invoice_id, customer_email, requested_by="ar-copilot-agent", cadence_days=cadence_days, end_date=end_date
        )
    except FollowUpError as exc:
        raise PolicyViolation(str(exc))

    return {
        "invoice_id": invoice_id,
        "action": "follow_up_started",
        "campaign_id": campaign_id,
        "customer_email": customer_email,
        "cadence_days": cadence_days,
        "end_date": end_date,
    }


SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "snooze_invoice",
        "description": (
            "Pause dunning on one invoice for a stated reason, optionally until a resume date. "
            "Cannot be used on an invoice still in the Pre-Due stage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "reason": {"type": "string"},
                "resume_date": {"type": "string", "description": "ISO date, e.g. 2026-08-01. Optional."},
            },
            "required": ["invoice_id", "reason"],
        },
    },
    {
        "name": "resume_invoice",
        "description": "Resume dunning on a previously-snoozed invoice.",
        "parameters": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
        },
    },
    {
        "name": "add_comment",
        "description": "Log a comment against an invoice.",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "comment": {"type": "string"},
            },
            "required": ["invoice_id", "comment"],
        },
    },
    {
        "name": "start_follow_up",
        "description": (
            "Start a recurring follow-up email campaign to the customer for one invoice: sends now, "
            "then repeats every cadence_days until cancelled or end_date passes. Refuses a second "
            "campaign while one is already active for the invoice."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "customer_email": {"type": "string"},
                "cadence_days": {"type": "integer", "description": "Send a follow-up every this many days."},
                "end_date": {"type": "string", "description": "ISO date, e.g. 2026-08-01. Optional -- runs until cancelled if omitted."},
            },
            "required": ["invoice_id", "customer_email", "cadence_days"],
        },
    },
]

HANDLERS = {
    "snooze_invoice": snooze_invoice,
    "resume_invoice": resume_invoice,
    "add_comment": add_comment,
    "start_follow_up": start_follow_up,
}
