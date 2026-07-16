"""
Read-only tools over the Lummus backend. No tool here has a side effect.

Field names match the real backend response schema
(backend/app/dunning_v2/api/schemas.py:CaseSummaryResponse) — verified
against source, not guessed. Notably: the backend has NO free-text
"search by customer/project name" filter server-side (only case-insensitive
substring match on invoice_no, case_key, business_unit_id, customer_id,
project_id). `list_invoices` therefore returns `project_name` (which IS
present per-case) for a reasonable batch and leaves natural-language
matching to the model — this is what makes invoice-ID-free resolution work
(PLAN.md §5 Phase 1): the model reasons over human-readable fields instead of
requiring a server-side name index.
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from app.services.backend_client import BackendClient

# ---------------------------------------------------------------------------
# Handlers — each takes the shared BackendClient plus its own kwargs.
# ---------------------------------------------------------------------------


async def list_invoices(
    client: BackendClient,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    business_unit_id: Optional[str] = None,
    project_id: Optional[str] = None,
    overdue_days_min: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List invoices/cases, optionally filtered. Returns human-readable
    fields (project_name, amounts, dates) so the caller can match on
    customer/project by name rather than needing an ID."""
    cases = await client.list_cases(
        case_status=status,
        current_stage_code=stage,
        business_unit_id=business_unit_id,
        project_id=project_id,
        limit=limit,
    )
    if overdue_days_min is not None:
        today = datetime.date.today()
        filtered = []
        for c in cases:
            due = c.get("primary_invoice_due_date")
            if not due:
                continue
            due_date = datetime.date.fromisoformat(due) if isinstance(due, str) else due
            if (today - due_date).days >= overdue_days_min:
                filtered.append(c)
        cases = filtered
    return [_summarize_case(c) for c in cases]


async def get_invoice(client: BackendClient, invoice_id: str) -> Dict[str, Any]:
    """Full detail for one invoice/case. `invoice_id` is the case's internal
    id — resolve it via list_invoices first, never ask the user for it."""
    case = await client.get_case(invoice_id)
    return _summarize_case(case, full=True)


async def get_timeline(client: BackendClient, invoice_id: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Event history for one invoice — comments, emails, stage transitions.
    Field names match the real backend response (CaseTimelineEventResponse)
    — verified against live UAT data, not guessed: the timestamp field is
    `occurred_at`, not `created_at`/`at`. An earlier version checked the
    wrong names, so every non-comment event (stage transitions, emails,
    case-opened) silently rendered with no timestamp in the UI."""
    events = await client.get_case_timeline(invoice_id, limit=limit)
    return [
        {
            "event_type": e.get("event_type"),
            "title": e.get("event_title"),
            "summary": e.get("event_summary"),
            "actor": e.get("actor_type"),
            "at": e.get("occurred_at"),
        }
        for e in events
    ]


async def get_project_contacts(client: BackendClient, project_number: str) -> List[Dict[str, Any]]:
    """PM/Finance/Legal contacts for a project, by project number."""
    contacts = await client.list_contacts_for_project(project_number)
    return [
        {
            "contact_type": c.get("contact_type"),
            "name": c.get("name"),
            "email": c.get("email"),
            "phone": c.get("phone"),
        }
        for c in contacts
    ]


async def list_review_tasks(client: BackendClient, invoice_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Open review tasks, optionally scoped to one invoice."""
    tasks = await client.list_review_tasks(case_id=invoice_id)
    return [
        {
            "id": t.get("id"),
            "case_id": t.get("case_id"),
            "reason": t.get("reason") or t.get("task_type"),
            "status": t.get("status"),
            "created_at": t.get("created_at"),
        }
        for t in tasks
    ]


async def aging_summary(client: BackendClient, business_unit_id: Optional[str] = None) -> Dict[str, Any]:
    """Totals by aging bucket and by stage, computed client-side from the
    case list — there's no dedicated summary endpoint."""
    cases = await client.list_cases(business_unit_id=business_unit_id, limit=500)

    by_bucket: Dict[str, Dict[str, Any]] = {}
    by_stage: Dict[str, Dict[str, Any]] = {}
    total_open = 0.0

    for c in cases:
        amt = c.get("primary_invoice_open_amount") or 0
        total_open += amt

        bucket = c.get("primary_invoice_aging_status") or "unknown"
        b = by_bucket.setdefault(bucket, {"count": 0, "open_amount": 0.0})
        b["count"] += 1
        b["open_amount"] += amt

        stage = c.get("current_stage_code") or "unknown"
        s = by_stage.setdefault(stage, {"count": 0, "open_amount": 0.0})
        s["count"] += 1
        s["open_amount"] += amt

    return {
        "total_invoices": len(cases),
        "total_open_amount": round(total_open, 2),
        "by_aging_bucket": by_bucket,
        "by_stage": by_stage,
    }


def _summarize_case(c: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    """Project the backend's CaseSummaryResponse down to what the model
    needs — human-readable fields first, the internal id kept but never
    the thing the model is instructed to ask the user for."""
    out = {
        "invoice_id": c.get("id"),
        "case_key": c.get("case_key"),
        "status": c.get("case_status"),
        "stage": c.get("current_stage_code"),
        "project_number": c.get("project_number"),
        "project_name": c.get("project_name"),
        "business_unit_id": c.get("business_unit_id"),
        "due_date": c.get("primary_invoice_due_date"),
        "open_amount": c.get("primary_invoice_open_amount"),
        "aging_status": c.get("primary_invoice_aging_status"),
    }
    if full:
        out["next_action_due_at"] = c.get("next_action_due_at")
        out["active_pause_id"] = c.get("active_pause_id")
        out["active_review_task_id"] = c.get("active_review_task_id")
        out["invoice_count"] = c.get("invoice_count")
    return out


# ---------------------------------------------------------------------------
# OpenAI tool-calling JSON schemas
# ---------------------------------------------------------------------------

SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "list_invoices",
        "description": (
            "List invoices (dunning cases), optionally filtered by status, "
            "stage, business unit, or minimum days overdue. Returns "
            "human-readable fields (project name, amount, due date) — use "
            "this to find an invoice by customer/project description "
            "instead of asking the user for an invoice number."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "e.g. active, closed_paid, closed_other"},
                "stage": {"type": "string", "description": "e.g. reminder, first_notice, second_notice, escalation, final_notice"},
                "business_unit_id": {"type": "string"},
                "project_id": {"type": "string", "description": "Project UUID or project number substring"},
                "overdue_days_min": {"type": "integer", "description": "Only invoices at least this many days past due"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "get_invoice",
        "description": "Full detail for one specific invoice, once resolved via list_invoices.",
        "parameters": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
        },
    },
    {
        "name": "get_timeline",
        "description": "Event history (comments, emails, stage changes) for one invoice.",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string"},
                "limit": {"type": "integer", "default": 25},
            },
            "required": ["invoice_id"],
        },
    },
    {
        "name": "get_project_contacts",
        "description": "PM/Finance/Legal contacts for a project.",
        "parameters": {
            "type": "object",
            "properties": {"project_number": {"type": "string"}},
            "required": ["project_number"],
        },
    },
    {
        "name": "list_review_tasks",
        "description": "Open review tasks, optionally scoped to one invoice.",
        "parameters": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
        },
    },
    {
        "name": "aging_summary",
        "description": "Totals by aging bucket and by dunning stage, optionally scoped to one business unit.",
        "parameters": {
            "type": "object",
            "properties": {"business_unit_id": {"type": "string"}},
        },
    },
]

HANDLERS = {
    "list_invoices": list_invoices,
    "get_invoice": get_invoice,
    "get_timeline": get_timeline,
    "get_project_contacts": get_project_contacts,
    "list_review_tasks": list_review_tasks,
    "aging_summary": aging_summary,
}
