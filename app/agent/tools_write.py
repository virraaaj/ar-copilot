"""
Write tools (PLAN.md §5 Phase 3): snooze_invoice, resume_invoice, add_comment.

Deliberately small blast radius (guardrails §6.6): each operates on exactly
one invoice per call, and every write requires non-empty text (a reason for
snooze/comment) — enforced by policy.py before the backend is ever called.
Registered in setup.py with extra_roles={"pm"}, so a PM can use these (via
Teams cards or chat) without getting the rest of the tool surface.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.guardrails.policy import check_nonempty_text, check_snooze_allowed
from app.services.backend_client import BackendClient


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
    source_channel: str = "ar_copilot",
) -> Dict[str, Any]:
    """Log a comment against an invoice."""
    check_nonempty_text(comment, "comment")
    result = await client.log_response_event(
        invoice_id, response_category="comment", raw_text=comment, source_channel=source_channel
    )
    return {"invoice_id": invoice_id, "action": "commented", "comment": comment, "result": result}


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
]

HANDLERS = {
    "snooze_invoice": snooze_invoice,
    "resume_invoice": resume_invoice,
    "add_comment": add_comment,
}
