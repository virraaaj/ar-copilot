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

from app.services.backend_client import BackendClient, BackendError
from app.services.chase_store import ChaseStore

# ---------------------------------------------------------------------------
# Handlers — each takes the shared BackendClient plus its own kwargs.
# ---------------------------------------------------------------------------


async def _project_name_map(client: BackendClient) -> Dict[str, Optional[str]]:
    """project_number -> project_name for every real project, sourced from
    the project-contacts listing (a genuine Project-table join, case-
    independent) rather than derived from cases -- every real project has
    at least a default contact seeded, so this covers projects a case
    listing would miss entirely."""
    projects = await client.list_all_project_contacts()
    return {p.get("project_number"): p.get("project_name") for p in projects if p.get("project_number")}


# Raw Invoice.status values -> the case_status-shaped vocabulary the
# frontend's status filter/pills already use, so a case-less invoice slots
# into the same filter UI without a separate status system.
_RAW_STATUS_MAP = {"Open": "active", "Paid": "closed_paid"}


def _summarize_raw_invoice(inv: Dict[str, Any], project_names: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """Projects a raw Invoice row (no dunning case yet) into the same shape
    _summarize_case produces, so the UI renders it identically -- added
    2026-07-24 after an uploaded invoice that wasn't overdue yet (so its
    case-feeder hadn't created a case for it) was invisible everywhere in
    the app, even though the invoice itself had synced fine. Case-only
    fields (case_key, stage, active_pause_id, ...) are simply absent/None;
    a chase/comments/timeline section naturally renders empty for these,
    the same as a real case with no activity yet would."""
    pn = inv.get("project_number")
    return {
        "invoice_id": inv.get("id"),
        "case_key": None,
        "invoice_no": inv.get("invoice_no"),
        "status": _RAW_STATUS_MAP.get(inv.get("status"), "closed_other"),
        "stage": None,
        "project_number": pn,
        "project_name": project_names.get(pn),
        "business_unit_id": None,
        "due_date": inv.get("due_date"),
        "open_amount": inv.get("amount"),
        "aging_status": "pre_due",
    }


async def list_invoices(
    client: BackendClient,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    business_unit_id: Optional[str] = None,
    project_id: Optional[str] = None,
    overdue_days_min: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """List invoices, optionally filtered. Returns human-readable fields
    (project_name, amounts, dates) so the caller can match on customer/
    project by name rather than needing an ID.

    Merges two sources (added 2026-07-24): /api/v2/dunning/cases (which
    only ever has invoices a case-feeder tick has already picked up --
    i.e. invoices that are actually overdue) and the raw, case-independent
    invoice table, so a just-uploaded invoice that isn't overdue yet still
    shows up instead of being invisible until its due date passes. Case-
    scoped filters (stage/business_unit_id/project_id) fall back to the
    case-only listing unchanged, since those concepts don't apply to a
    case-less invoice."""
    if stage or business_unit_id or project_id:
        cases = await client.list_cases(
            case_status=status,
            current_stage_code=stage,
            business_unit_id=business_unit_id,
            project_id=project_id,
            limit=limit,
        )
        merged = [_summarize_case(c) for c in cases]
    else:
        cases = await client.list_cases(limit=max(limit, 500))
        cases_by_invoice_no = {c.get("primary_invoice_id"): c for c in cases if c.get("primary_invoice_id")}
        raw_invoices = await client.list_invoices_raw(limit=max(limit, 1000))
        project_names = await _project_name_map(client)

        merged = []
        for inv in raw_invoices:
            case = cases_by_invoice_no.get(inv.get("invoice_no"))
            merged.append(_summarize_case(case) if case else _summarize_raw_invoice(inv, project_names))
        if status:
            merged = [m for m in merged if m["status"] == status]

    if overdue_days_min is not None:
        today = datetime.date.today()
        filtered = []
        for m in merged:
            due = m.get("due_date")
            if not due:
                continue
            due_date = datetime.date.fromisoformat(due) if isinstance(due, str) else due
            if (today - due_date).days >= overdue_days_min:
                filtered.append(m)
        merged = filtered
    return merged[:limit]


async def list_projects(client: BackendClient) -> List[Dict[str, Any]]:
    """Distinct projects (project_number/project_name pairs) -- sourced
    from the project-contacts listing (added 2026-07-24), a genuine
    case-independent Project-table join, not derived from cases. Every
    real project has contacts seeded, so this covers a project whose
    invoices don't have any dunning case yet, which deriving from cases
    would silently drop. Backs the Documents folder view and Chat's
    project picker."""
    project_names = await _project_name_map(client)
    return [
        {"project_number": pn, "project_name": name}
        for pn, name in sorted(project_names.items(), key=lambda kv: (kv[1] or kv[0]))
    ]


async def get_invoice(client: BackendClient, invoice_id: str) -> Dict[str, Any]:
    """Full detail for one invoice. `invoice_id` is either a case's
    internal id (when a dunning case exists) or the raw invoice's own id
    (when it doesn't yet) -- resolve it via list_invoices first, never ask
    the user for it. Tries the case lookup first (the common path, and the
    one every write action -- snooze/comment/chase -- actually needs);
    falls back to the raw invoice table for one that hasn't gotten a case
    yet (added 2026-07-24)."""
    try:
        case = await client.get_case(invoice_id)
        return _summarize_case(case, full=True)
    except BackendError:
        inv = await client.get_invoice_by_id(invoice_id)
        project_names = await _project_name_map(client)
        return _summarize_raw_invoice(inv, project_names)


async def get_timeline(client: BackendClient, invoice_id: str, limit: int = 25) -> List[Dict[str, Any]]:
    """Event history for one invoice — comments, emails, stage transitions.
    Field names match the real backend response (CaseTimelineEventResponse)
    — verified against live UAT data, not guessed: the timestamp field is
    `occurred_at`, not `created_at`/`at`. An earlier version checked the
    wrong names, so every non-comment event (stage transitions, emails,
    case-opened) silently rendered with no timestamp in the UI.

    A case-less invoice (no dunning case yet, added 2026-07-24) has no
    timeline to fetch -- returns empty rather than erroring, same as a
    real case with zero activity would render."""
    try:
        events = await client.get_case_timeline(invoice_id, limit=limit)
    except BackendError:
        return []
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


async def get_chase_status(client: BackendClient, invoice_id: str) -> Dict[str, Any]:
    """Status of the agentic chase engine's pursuit of one invoice, if
    any (PLAN_AGENTIC_CHASE.md) -- state, who's currently being chased,
    any promised payment date, and how many nudges/missed commitments so
    far. `client` is unused (chase state lives in ChaseStore, not the
    Lummus backend) but kept for handler-signature consistency with
    every other tool (AgentLoop always calls handler(backend, **args))."""
    store = ChaseStore()
    chase = await store.get_latest_for_case(invoice_id)
    if not chase:
        return {"invoice_id": invoice_id, "has_chase": False}

    return {
        "invoice_id": invoice_id,
        "has_chase": True,
        "state": chase["state"],
        "target": chase.get("target"),
        "promised_date": chase.get("promised_date"),
        "promised_by": chase.get("promised_by"),
        "missed_count": chase.get("missed_count"),
        "nudge_count": chase.get("nudge_count"),
        "last_outreach_at": chase.get("last_outreach_at"),
    }


def _summarize_case(c: Dict[str, Any], full: bool = False) -> Dict[str, Any]:
    """Project the backend's CaseSummaryResponse down to what the model
    needs — human-readable fields first, the internal id kept but never
    the thing the model is instructed to ask the user for."""
    out = {
        "invoice_id": c.get("id"),
        "case_key": c.get("case_key"),
        # The real, human-facing invoice number (e.g. "UAT-RND-FIN-002") --
        # distinct from case_key, which is an internal engine-generated
        # reference. Added 2026-07-16 so the UI can group/label invoices by
        # their actual invoice number instead of repeating the project name
        # per row. A case can technically span more than one invoice
        # (invoice_count on the full backend response); this is just the
        # primary one, same scope the rest of this projection already uses
        # for due_date/open_amount/aging_status.
        "invoice_no": c.get("primary_invoice_id"),
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
        "name": "list_projects",
        "description": "List every distinct project (project_number/project_name pairs) with at least one invoice.",
        "parameters": {"type": "object", "properties": {}},
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
    {
        "name": "get_chase_status",
        "description": (
            "Status of the automated agentic chase for one invoice, if any: which state it's in "
            "(e.g. waiting on the PM, waiting on the customer, a payment date is being tracked, "
            "escalated to a human), who's currently being chased, any promised payment date, and how "
            "many nudges/missed commitments so far. Use this when asked what's happening with the "
            "automated follow-up on a specific invoice."
        ),
        "parameters": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
        },
    },
]

HANDLERS = {
    "list_invoices": list_invoices,
    "list_projects": list_projects,
    "get_invoice": get_invoice,
    "get_timeline": get_timeline,
    "get_project_contacts": get_project_contacts,
    "list_review_tasks": list_review_tasks,
    "aging_summary": aging_summary,
    "get_chase_status": get_chase_status,
}
