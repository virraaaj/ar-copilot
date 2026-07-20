"""
Web channel: the FastAPI routes behind the UI (PLAN.md §5 Phase 2).

Auth is deliberately light — per §7 (RESOLVED): a single shared UAT login is
fine for now, this is a dev tool. "Login" here means: confirm the submitted
email/password are real Lummus credentials (via the real backend), then hand
back an opaque in-memory session token. No persistence, no refresh — restart
the process and everyone re-logs in. Actual data access still goes through
the shared BACKEND_SERVICE_EMAIL/PASSWORD service account (get_backend_client()),
not the logged-in user's own credentials — this session token is purely a
"does whoever's using this webpage actually have a Lummus login" gate.
"""
from __future__ import annotations

import json
import secrets
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.loop import AgentLoop, ToolCallRecord
from app.agent.setup import build_registry
from app.agent.tools_documents import get_document as tool_get_document
from app.agent.tools_documents import list_recent_documents as tool_list_recent_documents
from app.agent.tools_documents import search_documents as tool_search_documents
from app.agent.tools_read import aging_summary as tool_aging_summary
from app.agent.tools_read import get_invoice as tool_get_invoice
from app.agent.tools_read import get_project_digest as tool_get_project_digest
from app.agent.tools_read import get_timeline as tool_get_timeline
from app.agent.tools_read import list_invoices as tool_list_invoices
from app.agent.tools_write import add_comment as tool_add_comment
from app.agent.tools_write import resume_invoice as tool_resume_invoice
from app.agent.tools_write import snooze_invoice as tool_snooze_invoice
from app.config import get_settings
from app.documents.index import get_index
from app.documents.ingest import chunk_pages, extract_native_text
from app.documents.sources.manual_upload import ManualUploadSource
from app.guardrails.identity import resolve_role
from app.guardrails.magic_link import MagicLinkError, verify_magic_link_token
from app.guardrails.policy import (
    PolicyViolation,
    check_future_or_today,
    check_positive_int,
    check_valid_email,
)
from app.services.azure_openai import get_llm
from app.services.backend_client import BackendClient, BackendError, get_backend_client
from app.services.chase_store import ChaseStore
from app.services.followup_store import FollowUpError, FollowUpStore

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Auth (see module docstring — deliberately minimal)
# ---------------------------------------------------------------------------

_sessions: Dict[str, str] = {}  # session_token -> email


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    session_token: str
    email: str


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    s = get_settings()

    # TEMPORARY domain gate (see config.py) -- checked before touching the
    # backend at all, so a non-corehelix.ai email never even attempts a real
    # login. Case-insensitive; a bare "@domain" match, not a substring check
    # (so "corehelix.ai.evil.com" doesn't slip through).
    if s.ALLOWED_EMAIL_DOMAIN:
        domain = body.email.rsplit("@", 1)[-1].lower() if "@" in body.email else ""
        if domain != s.ALLOWED_EMAIL_DOMAIN.lower():
            raise HTTPException(status_code=403, detail=f"Only @{s.ALLOWED_EMAIL_DOMAIN} accounts can sign in right now.")

    client = BackendClient(s.BACKEND_API_URL, body.email, body.password)
    try:
        await client.verify_login()
    except BackendError:
        raise HTTPException(status_code=401, detail="Invalid Lummus credentials")
    finally:
        await client.close()

    token = secrets.token_urlsafe(24)
    _sessions[token] = body.email
    return LoginResponse(session_token=token, email=body.email)


def require_session(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")
    token = authorization[len("Bearer "):]
    email = _sessions.get(token)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return email


# ---------------------------------------------------------------------------
# Magic-link exchange (added 2026-07-16) — lets a Teams reminder-card button
# or chat redirect land the user already-authenticated on the web, instead
# of making them log in again right after Teams already vouched for them.
# See guardrails/magic_link.py for the token shape/verification and
# channels/teams/{cards,proactive,bot}.py for where these get generated.
# ---------------------------------------------------------------------------


class MagicLinkExchangeRequest(BaseModel):
    token: str


class MagicLinkExchangeResponse(BaseModel):
    session_token: str
    email: str
    redirect: str


@router.post("/auth/magic-link", response_model=MagicLinkExchangeResponse)
async def exchange_magic_link(body: MagicLinkExchangeRequest) -> MagicLinkExchangeResponse:
    try:
        payload = verify_magic_link_token(body.token)
    except MagicLinkError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    if payload.action in ("snooze", "comment", "follow_up"):
        redirect = f"/invoices/{payload.invoice_id}?action={payload.action}"
    else:  # pick_invoice
        redirect = f"/projects/{payload.project_number}/pick-invoice?action={payload.next_action}"

    token = secrets.token_urlsafe(24)
    _sessions[token] = payload.email
    return MagicLinkExchangeResponse(session_token=token, email=payload.email, redirect=redirect)


# ---------------------------------------------------------------------------
# Invoices (Dashboard, Invoice detail — read-only, PLAN.md §5 Phase 2)
# ---------------------------------------------------------------------------


@router.get("/invoices")
async def list_invoices_endpoint(
    status: Optional[str] = None,
    stage: Optional[str] = None,
    business_unit_id: Optional[str] = None,
    overdue_days_min: Optional[int] = None,
    limit: int = 50,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> List[Dict[str, Any]]:
    return await tool_list_invoices(
        backend, status=status, stage=stage, business_unit_id=business_unit_id,
        overdue_days_min=overdue_days_min, limit=limit,
    )


@router.get("/invoices/{invoice_id}")
async def get_invoice_endpoint(
    invoice_id: str,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    return await tool_get_invoice(backend, invoice_id=invoice_id)


@router.get("/projects/{project_number}/invoices")
async def list_project_invoices_endpoint(
    project_number: str,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> List[Dict[str, Any]]:
    """Backing the project invoice-picker page (added 2026-07-16): when a
    Teams user asks to snooze/comment in a project chat without naming a
    specific invoice, they land here to pick one from just that project's
    pooled invoices, matching the project-level group-chat model."""
    return await tool_list_invoices(backend, project_id=project_number, limit=200)


@router.get("/projects/{project_number}/digest")
async def get_project_digest_endpoint(
    project_number: str,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    """Backs the AR Health dashboard tab (added 2026-07-17): on-demand AR
    health + customer payment-pattern projections for one project -- the
    same computation the weekly Teams digest card uses (services/
    digest_engine.py), available whenever someone opens the tab rather
    than only once a week."""
    return await tool_get_project_digest(backend, project_number=project_number)


@router.get("/business-units")
async def list_business_units_endpoint(
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> List[Dict[str, Any]]:
    return await backend.list_business_units()


@router.get("/aging-summary")
async def aging_summary_endpoint(
    business_unit_id: Optional[str] = None,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    return await tool_aging_summary(backend, business_unit_id=business_unit_id)


# ---------------------------------------------------------------------------
# Project contacts + default project contacts (added 2026-07-16). Deliberately
# the same design/concept as Lummus's own equivalent pages
# (backend/app/api/v1/dunning.py's /project-contacts and
# /default-project-contacts) -- this is a thin pass-through to the same real
# endpoints, not a reimplementation: "default" contacts are a template (one
# Global scope + optional per-BU override scopes) that Lummus applies when
# seeding a *new* project's contacts, not a live link -- editing a default
# afterwards does not retroactively change any project's contacts.
# ---------------------------------------------------------------------------


class ProjectContactCreate(BaseModel):
    project_number: str
    contact_type: str
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class ProjectContactUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


@router.get("/project-contacts")
async def list_project_contacts_endpoint(
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> List[Dict[str, Any]]:
    return await backend.list_all_project_contacts()


@router.post("/project-contacts")
async def add_project_contact_endpoint(
    body: ProjectContactCreate,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    return await backend.add_project_contact(
        body.project_number, contact_type=body.contact_type, name=body.name, email=body.email, phone=body.phone
    )


@router.patch("/project-contacts/{contact_id}")
async def update_project_contact_endpoint(
    contact_id: str,
    body: ProjectContactUpdate,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return await backend.update_project_contact(contact_id, fields)


@router.delete("/project-contacts/{contact_id}")
async def delete_project_contact_endpoint(
    contact_id: str,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    return await backend.delete_project_contact(contact_id)


class DefaultProjectContactCreate(BaseModel):
    contact_type: str
    bu: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class DefaultProjectContactUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


@router.get("/default-project-contacts")
async def list_default_project_contacts_endpoint(
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> List[Dict[str, Any]]:
    return await backend.list_default_project_contacts()


@router.post("/default-project-contacts")
async def add_default_project_contact_endpoint(
    body: DefaultProjectContactCreate,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    return await backend.add_default_project_contact(
        contact_type=body.contact_type, bu=body.bu, name=body.name, email=body.email, phone=body.phone
    )


@router.patch("/default-project-contacts/{contact_id}")
async def update_default_project_contact_endpoint(
    contact_id: str,
    body: DefaultProjectContactUpdate,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    return await backend.update_default_project_contact(contact_id, fields)


@router.delete("/default-project-contacts/{contact_id}")
async def delete_default_project_contact_endpoint(
    contact_id: str,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    return await backend.delete_default_project_contact(contact_id)


# ---------------------------------------------------------------------------
# Invoice timeline + comments (added 2026-07-16). Channel-agnostic by
# construction: a comment logged here (source_channel="manual_only") and one
# logged from Teams (source_channel="teams", see channels/teams/bot.py) both
# land as reply_received events on the same backend timeline -- verified
# live against the real UAT backend, not assumed. This endpoint is just a
# window onto that same timeline; there is no separate "web comments" store.
# ---------------------------------------------------------------------------


class AddCommentRequest(BaseModel):
    comment: str


class SnoozeInvoiceRequest(BaseModel):
    reason: str
    resume_date: Optional[str] = None


@router.post("/invoices/{invoice_id}/snooze")
async def snooze_invoice_endpoint(
    invoice_id: str,
    body: SnoozeInvoiceRequest,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    try:
        return await tool_snooze_invoice(backend, invoice_id=invoice_id, reason=body.reason, resume_date=body.resume_date)
    except PolicyViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/invoices/{invoice_id}/resume")
async def resume_invoice_endpoint(
    invoice_id: str,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    """Resumes dunning outreach on a previously-snoozed invoice -- the
    counterpart to /snooze. The UI toggles between the two based on
    invoice.active_pause_id (non-null while a pause is in effect)."""
    return await tool_resume_invoice(backend, invoice_id=invoice_id)


# ---------------------------------------------------------------------------
# Manual follow-up email campaigns (added 2026-07-16). "Follow up with the
# customer" (Teams chat, or the web) sets up a real email sent via
# followup_engine.py's poller -- see that module and services/followup_store.py
# for the full design. One active campaign per invoice; the send history
# lives locally (see followup_store.py's docstring for why it's not on the
# real Lummus timeline) and is merged into the UI's Activity view.
# ---------------------------------------------------------------------------


class CreateFollowUpRequest(BaseModel):
    customer_email: str
    cadence_days: int
    end_date: Optional[str] = None  # ISO date, optional -- omitted means "until cancelled"


@router.get("/invoices/{invoice_id}/follow-up")
async def get_follow_up_status(
    invoice_id: str,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    store = FollowUpStore()
    active = await store.get_active_for_case(invoice_id)
    history = await store.list_send_history(invoice_id)
    return {"active_campaign": active, "send_history": history}


@router.post("/invoices/{invoice_id}/follow-up")
async def create_follow_up(
    invoice_id: str,
    body: CreateFollowUpRequest,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    try:
        check_valid_email(body.customer_email)
        check_positive_int(body.cadence_days, "cadence_days")
        check_future_or_today(body.end_date, "end_date")
    except PolicyViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    store = FollowUpStore()
    try:
        campaign_id = await store.create(
            invoice_id, body.customer_email, requested_by=_user, cadence_days=body.cadence_days, end_date=body.end_date
        )
    except FollowUpError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"campaign_id": campaign_id}


@router.post("/invoices/{invoice_id}/follow-up/cancel")
async def cancel_follow_up(
    invoice_id: str,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    store = FollowUpStore()
    cancelled = await store.cancel(invoice_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="No active follow-up campaign for this invoice.")
    return {"ok": True}


@router.get("/invoices/{invoice_id}/timeline")
async def get_invoice_timeline_endpoint(
    invoice_id: str,
    limit: int = 25,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> List[Dict[str, Any]]:
    return await tool_get_timeline(backend, invoice_id=invoice_id, limit=limit)


@router.post("/invoices/{invoice_id}/comments")
async def add_comment_endpoint(
    invoice_id: str,
    body: AddCommentRequest,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    """The real backend's timeline has no per-comment "who posted this"
    field usable here: every write goes through one shared service account
    (see module docstring), so a comment's actor_id on the backend is
    always that service account, never the actual logged-in/Teams user.
    The only place that identity survives is the comment text itself, so
    it's prefixed here in a fixed, parseable shape ("[email] text") --
    the UI (InvoiceDetail.tsx) parses it back out to show the author
    separately. Every comment now flows through this one endpoint
    (including ones that started from a Teams magic link, which carries
    the same-shaped session), so this is the single place to do it."""
    # Validated on the raw text, before the "[email] " prefix is added --
    # otherwise a whitespace-only comment would always pass the tool's own
    # non-empty check once prefixed, since the prefix alone is non-empty.
    if not body.comment.strip():
        raise HTTPException(status_code=422, detail="comment must not be empty")

    prefixed = f"[{_user}] {body.comment}"
    try:
        return await tool_add_comment(backend, invoice_id=invoice_id, comment=prefixed, source_channel="manual_only")
    except PolicyViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# Escalation policy (added 2026-07-16) — a real client to the Lummus backend's
# existing dunning_v2 policy API (backend/app/dunning_v2/api/policies.py),
# not a new capability invented here. Edits apply directly to the current
# published version (confirmed against that module's own comment: "Edits are
# accepted on draft and published versions") — no separate draft/validate/
# publish workflow surfaced here, since this page is meant as a simple global
# settings screen, not the full policy-authoring tool the Lummus admin pages
# already are.
# ---------------------------------------------------------------------------


class StageThresholdUpdate(BaseModel):
    max_days_in_stage: Optional[int] = None
    min_days_in_stage: Optional[int] = None


async def _find_global_policy(backend: BackendClient) -> Dict[str, Any]:
    policies = await backend.list_policies(scope_type="global")
    if not policies:
        raise HTTPException(status_code=404, detail="No global escalation policy found.")
    return policies[0]


async def _find_published_version(backend: BackendClient, policy_id: str) -> Dict[str, Any]:
    versions = await backend.list_policy_versions(policy_id)
    published = [v for v in versions if v.get("is_current_published")]
    if not published:
        raise HTTPException(status_code=404, detail="This policy has no published version.")
    return published[0]


@router.get("/escalation-policy")
async def get_escalation_policy(
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    policy = await _find_global_policy(backend)
    version = await _find_published_version(backend, policy["id"])
    stages = await backend.list_policy_stages(version["id"])
    rules = await backend.list_stage_rules(version["id"])
    rules_by_stage = {r["stage_id"]: r for r in rules}

    stage_list = []
    for s in sorted(stages, key=lambda x: x["sequence_order"]):
        rule = rules_by_stage.get(s["id"])
        transition = (rule or {}).get("transition_rule_json") or {}
        stage_list.append(
            {
                "stage_id": s["id"],
                "stage_rule_id": rule["id"] if rule else None,
                "stage_code": s["stage_code"],
                "stage_name": s["stage_name"],
                "sequence_order": s["sequence_order"],
                "is_terminal_stage": s["is_terminal_stage"],
                "min_days_in_stage": transition.get("min_days_in_stage"),
                "max_days_in_stage": transition.get("max_days_in_stage"),
            }
        )

    return {
        "policy_id": policy["id"],
        "version_id": version["id"],
        "version_label": version.get("display_label"),
        "stages": stage_list,
    }


@router.patch("/escalation-policy/versions/{version_id}/stage-rules/{stage_rule_id}")
async def update_escalation_stage(
    version_id: str,
    stage_rule_id: str,
    body: StageThresholdUpdate,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    # The backend's PATCH replaces transition_rule_json wholesale, so fetch
    # the current value fresh and merge in just what changed -- never trust
    # client-side state as the merge base, it may be stale.
    rules = await backend.list_stage_rules(version_id)
    current = next((r for r in rules if r["id"] == stage_rule_id), None)
    if current is None:
        raise HTTPException(status_code=404, detail="Stage rule not found in that policy version.")

    transition = dict(current.get("transition_rule_json") or {})
    if body.max_days_in_stage is not None:
        transition["max_days_in_stage"] = body.max_days_in_stage
    if body.min_days_in_stage is not None:
        transition["min_days_in_stage"] = body.min_days_in_stage

    result = await backend.update_stage_rule(stage_rule_id, {"transition_rule_json": transition})
    return {"stage_rule_id": stage_rule_id, "transition_rule_json": result.get("transition_rule_json")}


# ---------------------------------------------------------------------------
# Documents (PLAN.md §5 Phase 1B UI)
# ---------------------------------------------------------------------------


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    doc_type: Optional[str] = None,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    content = await file.read()
    source = ManualUploadSource()
    ref = await source.save(file.filename, content, doc_type=doc_type)

    pages = extract_native_text(content)
    chunks = chunk_pages(pages)
    index = get_index()
    await index.add_document(ref.source_id, ref.filename, ref.doc_type, chunks)

    empty_pages = sum(1 for p in pages if not p.text)
    return {
        "filename": ref.filename,
        "doc_type": ref.doc_type,
        "page_count": len(pages),
        "chunks_indexed": len(chunks),
        "pages_needing_ocr": empty_pages,  # non-zero -> AZURE_DOCUMENT_INTELLIGENCE_KEY needed for full coverage
    }


@router.get("/documents")
async def list_documents_endpoint(
    doc_type: Optional[str] = None,
    _user: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    return await tool_list_recent_documents(None, doc_type=doc_type)


@router.get("/documents/search")
async def search_documents_endpoint(
    query: str,
    doc_type: Optional[str] = None,
    top_k: int = 5,
    _user: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    return await tool_search_documents(None, query=query, doc_type=doc_type, top_k=top_k)


# ---------------------------------------------------------------------------
# Agentic chase engine (PLAN_AGENTIC_CHASE.md Phase C4) — the web Chases
# tab: every chase's current state + full event history, plus the human
# actions available once one escalates (pause/resume/close/restart/edit
# the tracked commitment). This is a thin read/write window onto
# ChaseStore -- all the actual chase-progression logic lives in
# chase_engine.py/chase_machine.py, untouched here.
# ---------------------------------------------------------------------------


class EditCommitmentRequest(BaseModel):
    promised_date: str  # ISO date


@router.get("/chases")
async def list_chases_endpoint(
    state: Optional[str] = None,
    _user: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    store = ChaseStore()
    return await store.list_all(state=state)


@router.get("/chases/{chase_id}")
async def get_chase_endpoint(
    chase_id: str,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    store = ChaseStore()
    chase = await store.get(chase_id)
    if not chase:
        raise HTTPException(status_code=404, detail="Chase not found.")
    return chase


@router.get("/chases/{chase_id}/events")
async def list_chase_events_endpoint(
    chase_id: str,
    _user: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    store = ChaseStore()
    return await store.list_events(chase_id)


@router.post("/chases/{chase_id}/pause")
async def pause_chase_endpoint(
    chase_id: str,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    store = ChaseStore()
    if not await store.get(chase_id):
        raise HTTPException(status_code=404, detail="Chase not found.")
    await store.update(chase_id, state="paused", next_action_at=None)
    await store.add_event(chase_id, "human_action", {"action": "pause", "by": _user})
    return {"ok": True}


@router.post("/chases/{chase_id}/resume")
async def resume_chase_endpoint(
    chase_id: str,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    """Resumes a paused (or escalated) chase back onto the nudge cadence
    -- next_action_at set to now so the very next poll tick picks it back
    up, same "acts immediately" convention as followup_store.create()."""
    store = ChaseStore()
    chase = await store.get(chase_id)
    if not chase:
        raise HTTPException(status_code=404, detail="Chase not found.")
    resume_state = chase.get("target") and f"awaiting_{chase['target']}" or "pending"
    await store.update(chase_id, state=resume_state, next_action_at=_now_iso())
    await store.add_event(chase_id, "human_action", {"action": "resume", "by": _user})
    return {"ok": True}


class CloseChaseRequest(BaseModel):
    reason: str


@router.post("/chases/{chase_id}/close")
async def close_chase_endpoint(
    chase_id: str,
    body: CloseChaseRequest,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    store = ChaseStore()
    if not await store.get(chase_id):
        raise HTTPException(status_code=404, detail="Chase not found.")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="reason is required.")
    await store.update(chase_id, state="closed_manual", next_action_at=None)
    await store.add_event(chase_id, "human_action", {"action": "close", "reason": body.reason, "by": _user})
    return {"ok": True}


@router.post("/chases/{chase_id}/restart")
async def restart_chase_endpoint(
    chase_id: str,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    """Re-opens a closed/escalated chase from scratch (state=pending) --
    for when a human resolves the underlying issue (e.g. got the customer
    email manually) and wants the automated chase to pick back up."""
    store = ChaseStore()
    chase = await store.get(chase_id)
    if not chase:
        raise HTTPException(status_code=404, detail="Chase not found.")
    await store.update(
        chase_id, state="pending", target=None, promised_date=None, promised_by=None,
        missed_count=0, nudge_count=0, clarify_count=0, next_action_at=_now_iso(),
    )
    await store.add_event(chase_id, "human_action", {"action": "restart", "by": _user})
    return {"ok": True}


@router.patch("/chases/{chase_id}/commitment")
async def edit_chase_commitment_endpoint(
    chase_id: str,
    body: EditCommitmentRequest,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    try:
        check_future_or_today(body.promised_date, "promised_date")
    except PolicyViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    store = ChaseStore()
    chase = await store.get(chase_id)
    if not chase:
        raise HTTPException(status_code=404, detail="Chase not found.")
    await store.update(chase_id, state="commitment_tracked", promised_date=body.promised_date, promised_by="pm")
    await store.add_event(
        chase_id, "human_action", {"action": "edit_commitment", "promised_date": body.promised_date, "by": _user}
    )
    return {"ok": True}


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


# ---------------------------------------------------------------------------
# Chat (SSE) — PLAN.md §5 Phase 2, Phase 1's invoice-ID-free resolution
# ---------------------------------------------------------------------------


class PinnedInvoice(BaseModel):
    invoice_id: str
    label: str  # human-readable, e.g. "Meridian Bay -- $1.25M, 21 days overdue"


class ChatRequest(BaseModel):
    message: str
    pinned_invoice: Optional[PinnedInvoice] = None
    history: Optional[List[Dict[str, Any]]] = None


def _sse_event(event_type: str, data: Dict[str, Any]) -> str:
    return f"data: {json.dumps({'type': event_type, **data}, default=str)}\n\n"


@router.post("/chat")
async def chat(
    body: ChatRequest,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> StreamingResponse:
    registry = build_registry()
    llm = get_llm()
    loop = AgentLoop(llm=llm, registry=registry, backend_client=backend)

    history = list(body.history or [])
    if body.pinned_invoice:
        # The ID rides along in the actual API call; the UI only ever showed
        # the human-readable label to the user (PLAN.md's invoice-ID-free
        # resolution principle, Phase 2 half of it).
        history.append(
            {
                "role": "system",
                "content": f"The user is asking about invoice_id={body.pinned_invoice.invoice_id} ({body.pinned_invoice.label}).",
            }
        )

    async def stream() -> AsyncIterator[str]:
        events: List[str] = []

        async def collect_tool_call(record: ToolCallRecord) -> None:
            events.append(_sse_event("tool_call", {"name": record.name, "permitted": record.permitted}))

        # AgentLoop.run() isn't itself a generator, so tool-call events are
        # buffered by the on_tool_call hook and flushed in order, followed by
        # the final answer -- real progress updates, just not token-by-token
        # (that needs streaming support in AzureOpenAIService.chat() itself,
        # not yet built -- there's no live model to stream from yet anyway).
        result = await loop.run(body.message, role=resolve_role(_user), history=history, on_tool_call=collect_tool_call)
        for e in events:
            yield e
        yield _sse_event("answer", {"content": result.answer, "truncated": result.truncated})

    return StreamingResponse(stream(), media_type="text/event-stream")
