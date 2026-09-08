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
import os
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
from app.agent.tools_read import get_timeline as tool_get_timeline
from app.agent.tools_read import list_invoices as tool_list_invoices
from app.agent.tools_read import list_projects as tool_list_projects
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
from app.channels.teams.messenger import get_messenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.services.email_sender import get_email_sender
from app.services.followup_store import FollowUpError, FollowUpStore
from app.services import sim_clock
from app.services.uat_reset import wipe_uat_data
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.loop.invoice_sync import sync_invoices_to_cases

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
    # True when DEV_AUTH_BYPASS issued the session (offline Outcome Agent demo).
    demo_mode: bool = False


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

    if not s.DEV_AUTH_BYPASS:
        client = BackendClient(s.BACKEND_API_URL, body.email, body.password)
        try:
            await client.verify_login()
        except BackendError:
            raise HTTPException(status_code=401, detail="Invalid Lummus credentials")
        finally:
            await client.close()

    token = secrets.token_urlsafe(24)
    _sessions[token] = body.email
    return LoginResponse(session_token=token, email=body.email, demo_mode=bool(s.DEV_AUTH_BYPASS))


def require_session(authorization: Optional[str] = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")
    token = authorization[len("Bearer "):]
    email = _sessions.get(token)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return email


# Outcome Agent routes (/api/agent/*) + /api/chases compat aliases
from app.outcome_agent.api.routes import build_agent_router  # noqa: E402

router.include_router(build_agent_router(require_session))


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
    # Offline Outcome Agent demo: Dashboard must not require Lummus UAT.
    if get_settings().DEV_AUTH_BYPASS:
        return []
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


@router.get("/business-units")
async def list_business_units_endpoint(
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> List[Dict[str, Any]]:
    return await backend.list_business_units()


@router.get("/aging/last-synced")
async def aging_last_synced_endpoint(
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    """Real, server-verified freshness signal for the Dashboard's sync badge
    (added 2026-09-07, replacing a client-side localStorage proxy that only
    reflected this browser's own last upload -- see Dashboard.tsx history).

    `/api/v1/dunning/aging-table` rows each carry an `as_of_date` -- the
    snapshot date of the aging upload that produced that row. The most
    recent aging upload's date is simply the maximum `as_of_date` across all
    rows. That endpoint does not document (and we have no evidence of) a
    guaranteed sort order by `as_of_date`, so a small `limit` could silently
    miss the true max -- this pulls the full, paginated table via
    `list_aging_table()` to keep the answer correct rather than fast. If
    this ever needs to be cheaper, the real fix is asking Lummus for a
    dedicated max-as_of_date endpoint or a documented sort order, not
    guessing with a small page here.

    No rows at all -> {"as_of_date": None}, not an error. An upstream
    failure is left to propagate as httpx.RequestError, which app/main.py's
    handler turns into a clean 503 -- never swallowed into a fake date.
    """
    rows = await backend.list_aging_table()
    dates = [r["as_of_date"] for r in rows if r.get("as_of_date")]
    return {"as_of_date": max(dates) if dates else None}


@router.get("/aging-summary")
async def aging_summary_endpoint(
    business_unit_id: Optional[str] = None,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    if get_settings().DEV_AUTH_BYPASS:
        return {
            "total_invoices": 0,
            "total_open_amount": 0.0,
            "by_aging_bucket": {},
            "by_stage": {},
        }
    return await tool_aging_summary(backend, business_unit_id=business_unit_id)


# ---------------------------------------------------------------------------
# Aging-table upload (added 2026-07-22): lets a PM upload a real Hubble-
# shaped "AR As-of 3rd Party with Location" Excel export directly from the
# Dashboard instead of needing a script -- same two real backend calls
# used manually all session (sync_aging_excel + trigger_tick), just now
# reachable from the UI. trigger_tick is documented as test-mode only
# (backend_client.py); this app only ever points at the UAT stack, where
# that's exactly the intended usage -- production Lummus ticks on its own
# schedule and wouldn't need this second call at all.
# ---------------------------------------------------------------------------


@router.post("/aging-upload")
async def upload_aging_excel(
    file: UploadFile = File(...),
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    content = await file.read()
    sync_result = await backend.sync_aging_excel(file.filename, content)

    tick_result: Optional[Dict[str, Any]] = None
    tick_error: Optional[str] = None
    try:
        tick_result = await backend.trigger_tick()
    except BackendError as exc:
        # Non-UAT backends won't have the test-mode tick endpoint enabled --
        # the aging sync itself still succeeded, so surface that partial
        # success rather than failing the whole upload.
        tick_error = str(exc)

    # Outcome Agent case sync (added 2026-08-03): nothing else on this
    # branch ever turns an uploaded invoice into an oa_cases row, so an
    # upload alone never produced a chaseable case -- only Reset Demo's
    # canned seed did. Best-effort: an upload that already succeeded above
    # shouldn't fail just because this bridge hit an error.
    agent_sync_result: Optional[Dict[str, Any]] = None
    agent_sync_error: Optional[str] = None
    try:
        agent_sync_result = await sync_invoices_to_cases(backend)
    except Exception as exc:  # noqa: BLE001
        agent_sync_error = str(exc)

    return {
        "sync": sync_result,
        "tick": tick_result,
        "tick_error": tick_error,
        "agent_sync": agent_sync_result,
        "agent_sync_error": agent_sync_error,
    }


@router.post("/agent/sync-invoices")
async def sync_invoices_endpoint(
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    """Manual re-trigger for sync_invoices_to_cases -- e.g. after an
    upload done outside this app, or to retry if the automatic sync in
    /aging-upload hit an error."""
    return await sync_invoices_to_cases(backend)


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
# Documents (PLAN.md §5 Phase 1B UI)
# ---------------------------------------------------------------------------


@router.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    project_number: str = "",
    doc_type: Optional[str] = None,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    # Project folders (added 2026-07-23): every upload belongs to exactly one
    # project going forward -- FastAPI can't express "required query param"
    # cleanly alongside a multipart file body without Form(), so this is
    # enforced with an explicit check instead of Query(...).
    if not project_number:
        raise HTTPException(status_code=400, detail="project_number is required.")

    content = await file.read()
    source = ManualUploadSource()
    ref = await source.save(file.filename, content, doc_type=doc_type, project_number=project_number)

    pages = extract_native_text(content)
    chunks = chunk_pages(pages)
    index = get_index()
    await index.add_document(ref.source_id, ref.filename, ref.doc_type, chunks, project_number=project_number)

    empty_pages = sum(1 for p in pages if not p.text)
    return {
        "filename": ref.filename,
        "doc_type": ref.doc_type,
        "project_number": ref.project_number,
        "page_count": len(pages),
        "chunks_indexed": len(chunks),
        "pages_needing_ocr": empty_pages,  # non-zero -> AZURE_DOCUMENT_INTELLIGENCE_KEY needed for full coverage
    }


@router.get("/documents")
async def list_documents_endpoint(
    doc_type: Optional[str] = None,
    project_number: Optional[str] = None,
    _user: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    return await tool_list_recent_documents(None, doc_type=doc_type, project_number=project_number)


@router.get("/documents/search")
async def search_documents_endpoint(
    query: str,
    doc_type: Optional[str] = None,
    project_number: Optional[str] = None,
    top_k: int = 5,
    _user: str = Depends(require_session),
) -> List[Dict[str, Any]]:
    return await tool_search_documents(None, query=query, doc_type=doc_type, top_k=top_k, project_number=project_number)


@router.get("/projects")
async def list_projects_endpoint(
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> List[Dict[str, Any]]:
    """Distinct projects derived from invoice data -- backs the Documents
    folder view and Chat's project picker (added 2026-07-23)."""
    return await tool_list_projects(backend)


# ---------------------------------------------------------------------------
# Agentic chase engine routes moved to app/outcome_agent/api/routes.py
# (included above via build_agent_router). Compat /chases/* aliases live there.
# ---------------------------------------------------------------------------


class EditCommitmentRequest(BaseModel):
    promised_date: str  # ISO date


@router.patch("/agent/cases/{case_row_id}/commitment")
async def edit_case_commitment_endpoint(
    case_row_id: str,
    body: EditCommitmentRequest,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    try:
        check_future_or_today(body.promised_date, "promised_date")
    except PolicyViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    store = CaseStore()
    case = await store.get(case_row_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    commitments = list(case.get("commitments") or [])
    for c in commitments:
        if c.get("status") == "active":
            c["status"] = "superseded"
    commitments.append(
        {
            "id": f"cmt-human-{body.promised_date}",
            "case_id": case.get("case_id"),
            "type": "payment_date",
            "date": body.promised_date,
            "owner": "pm",
            "status": "active",
            "source": "human",
            "confidence": 1.0,
            "miss_consequence": "re-engage or escalate",
        }
    )
    await store.update(case_row_id, state="promise_to_pay", commitments=commitments)
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


class ProjectRef(BaseModel):
    project_number: str
    project_name: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    # Required (project-scoped chat, added 2026-07-23): every conversation
    # is about exactly one project now, enforced here as well as by the UI
    # (Chat.tsx's project picker) -- defense in depth, not just a client rule.
    project: ProjectRef
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
    # Project scope (added 2026-07-23) -- same history-injection mechanism
    # as the pinned-invoice message below, added first so it reads as the
    # broader context the pinned invoice (if any) narrows further within.
    history.append(
        {
            "role": "system",
            "content": (
                f"This conversation is scoped to project_number={body.project.project_number} "
                f"({body.project.project_name or body.project.project_number}). When listing invoices "
                "or searching/listing documents, always filter to this project unless the user clearly "
                "asks about something else."
            ),
        }
    )
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


# ---------------------------------------------------------------------------
# UAT data reset (dev-only, added 2026-07-23). Truncates every table in the
# Lummus UAT Postgres database except users and default_project_contacts
# (see uat_reset.py's KEEP_TABLES), then clears ar-copilot's own local
# state.db too -- otherwise the chase-engine poller's next tick would just
# repopulate chases from whatever it had already cached, and the Chases tab
# would look untouched even though the source data is gone.
# ---------------------------------------------------------------------------


class WipeUatDataRequest(BaseModel):
    # Requires the literal confirm string rather than just a boolean --
    # makes it much harder to trigger by an automated retry, a copy-pasted
    # curl command with a stale body, or a fat-fingered request.
    confirm: str


@router.post("/admin/wipe-uat-data")
async def wipe_uat_data_endpoint(
    body: WipeUatDataRequest,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    if resolve_role(_user) != "admin":
        raise HTTPException(status_code=403, detail="Admin only.")
    if body.confirm != "WIPE_UAT_DATA":
        raise HTTPException(status_code=400, detail='Send {"confirm": "WIPE_UAT_DATA"} to proceed.')

    s = get_settings()
    if not s.UAT_DATABASE_URL:
        raise HTTPException(status_code=400, detail="UAT_DATABASE_URL is not configured.")

    result = await wipe_uat_data(s.UAT_DATABASE_URL)

    local_state_cleared = False
    if os.path.exists(s.STATE_DB_PATH):
        os.remove(s.STATE_DB_PATH)
        local_state_cleared = True

    return {**result, "local_state_cleared": local_state_cleared}


# ---------------------------------------------------------------------------
# Simulation clock (long-horizon outcome agent spec §6.16, added 2026-07-25).
# Lets an operator advance the demo's notion of "today" without waiting for
# real days to pass -- every chase-engine date decision (chase_machine.py's
# `now` parameter, chase_engine.py's due-tick/creation-eligibility checks)
# reads this same clock via app/services/sim_clock.py. Advancing it does NOT
# by itself process anything due -- the operator (or the normal poller tick)
# still has to trigger a chase-engine run afterward to see the effect, same
# as any other tick.
# ---------------------------------------------------------------------------


class AdvanceClockRequest(BaseModel):
    days: int


class JumpClockRequest(BaseModel):
    date: str  # ISO date or datetime


def _clock_state(current: Any, simulated: bool) -> Dict[str, Any]:
    return {"now": current.isoformat(), "is_simulated": simulated}


@router.get("/sim-clock")
async def get_sim_clock_endpoint(_user: str = Depends(require_session)) -> Dict[str, Any]:
    store = CaseStore()
    current = await sim_clock.now(store.db_path)
    simulated = await sim_clock.is_simulated(store.db_path)
    return _clock_state(current, simulated)


@router.post("/sim-clock/advance")
async def advance_sim_clock_endpoint(
    body: AdvanceClockRequest, _user: str = Depends(require_session)
) -> Dict[str, Any]:
    if body.days <= 0:
        raise HTTPException(status_code=400, detail="days must be positive.")
    store = CaseStore()
    new_time = await sim_clock.advance_days(body.days, store.db_path)
    from app.outcome_agent.loop.scheduler import run_agent_tick

    tick = await run_agent_tick(get_settings(), db_path=store.db_path)
    state = _clock_state(new_time, True)
    state["tick"] = tick
    return state


@router.post("/sim-clock/jump")
async def jump_sim_clock_endpoint(
    body: JumpClockRequest, _user: str = Depends(require_session)
) -> Dict[str, Any]:
    """Jump straight to a specific date and run a tick -- the frontend
    already called this (jumpSimClock in api.ts) but the route never
    existed, so "skip to next event" silently 404'd. Added 2026-08-06 to
    support Trace Studio's "skip to next event" button, which jumps to a
    case's own next_action_at instead of clicking +1d/+3d/+7d repeatedly."""
    from datetime import datetime as _dt

    try:
        target = _dt.fromisoformat(body.date.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be an ISO date or datetime.")
    store = CaseStore()
    await sim_clock.set_simulated_at(target, store.db_path)
    from app.outcome_agent.loop.scheduler import run_agent_tick

    tick = await run_agent_tick(get_settings(), db_path=store.db_path)
    state = _clock_state(target, True)
    state["tick"] = tick
    return state


@router.post("/sim-clock/reset")
async def reset_sim_clock_endpoint(_user: str = Depends(require_session)) -> Dict[str, Any]:
    store = CaseStore()
    await sim_clock.reset_to_real_time(store.db_path)
    current = await sim_clock.now(store.db_path)
    return _clock_state(current, False)


@router.get("/outcome-definition")
async def get_outcome_definition_endpoint(_user: str = Depends(require_session)) -> Dict[str, Any]:
    """Outcome definition from outcome_agent policy (principles + hard rules)."""
    from app.outcome_agent.config.collections_outcome import outcome_definition
    from app.outcome_agent.config.policies import policy_from_settings

    return outcome_definition(policy_from_settings(get_settings()))


@router.get("/policy-config")
async def get_policy_config_endpoint(_user: str = Depends(require_session)) -> Dict[str, Any]:
    """Policy/Configuration Viewer — outcome agent + kill switches."""
    from app.outcome_agent.config.policies import policy_from_settings
    from app.services.chase_guardrails import BLACKOUT_DATES
    from app.services import runtime_flags

    s = get_settings()
    p = policy_from_settings(s)
    composer_enabled = await runtime_flags.effective("CHASE_COMPOSER_ENABLED", s)
    smart_escalation_enabled = await runtime_flags.effective("CHASE_SMART_ESCALATION_ENABLED", s)
    return {
        "contact_frequency": {
            "nudge_interval_days": p.nudge_interval_days,
            "max_nudges": p.max_unanswered,
            "min_hours_between_touches": s.OUTCOME_AGENT_MIN_HOURS_BETWEEN_TOUCHES
            or s.CHASE_MIN_HOURS_BETWEEN_TOUCHES,
        },
        "escalation_rules": {
            "max_missed_commitments": p.max_missed_promises,
            "max_commitment_days": p.max_commitment_days,
            "grace_days": p.grace_days,
            "payment_verify_days": p.payment_verify_days,
            "max_clarifications": 1,
            "max_postponements": p.max_postponements,
            "blackout_dates": sorted(str(d) for d in BLACKOUT_DATES),
        },
        "allowed_actions": [
            "send_message", "schedule_follow_up", "escalate", "mark_paid",
            "create_dispute", "clarify", "verify_payment",
        ],
        "channel_configuration": {
            "outcome_agent_enabled": s.OUTCOME_AGENT_ENABLED,
            "outcome_agent_enabled_effective": s.outcome_agent_enabled_effective,
            "chase_enabled": s.CHASE_ENABLED,
            "mail_poll_enabled": s.OUTCOME_AGENT_MAIL_POLL_ENABLED or s.CHASE_MAIL_POLL_ENABLED,
            "composer_enabled": composer_enabled,
            "smart_escalation_enabled": smart_escalation_enabled,
            "to_address_allowlist": s.outcome_agent_to_address_allowlist,
            "max_sends_per_tick": p.max_sends_per_tick,
        },
    }


# ---------------------------------------------------------------------------
# Editable agent policy (added 2026-08-06, user feedback): every timing/
# threshold knob on AgentPolicy, customizable at a per-project level with a
# global default underneath -- see app/outcome_agent/config/policy_overrides.py
# for the resolution order and the (deliberate) set of fields excluded from
# override (allowlists -- deploy-time safety decisions, not UI toggles,
# same boundary runtime_flags.py already draws).
# ---------------------------------------------------------------------------


@router.get("/agent/policy")
async def get_agent_policy_endpoint(
    project_number: Optional[str] = None,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    from app.outcome_agent.config.policy_overrides import (
        OVERRIDABLE_FIELDS,
        effective_policy,
        resolve_field,
    )

    store = CaseStore()
    settings = get_settings()
    policy = await effective_policy(project_number, settings, db_path=store.db_path)
    field_map = {
        "max_unanswered": policy.max_unanswered,
        "max_missed_promises": policy.max_missed_promises,
        "max_postponements": policy.max_postponements,
        "max_commitment_days": policy.max_commitment_days,
        "grace_days": policy.grace_days,
        "payment_verify_days": policy.payment_verify_days,
        "nudge_interval_days": policy.nudge_interval_days,
        "min_hours_between_touches": policy.min_days_between_emails * 24,
        "max_sends_per_tick": policy.max_sends_per_tick,
        "high_dollar_threshold": policy.high_dollar_threshold,
        "composer_enabled": policy.composer_enabled,
        "smart_escalation_enabled": policy.smart_escalation_enabled,
    }
    fields: Dict[str, Any] = {}
    for field in OVERRIDABLE_FIELDS:
        source = await resolve_field(field, project_number, db_path=store.db_path)
        fields[field] = {"value": field_map[field], "source": source["source"]}
    return {"project_number": project_number, "fields": fields}


class SetPolicyOverrideRequest(BaseModel):
    field: str
    value: Any


@router.put("/agent/policy/default")
async def set_default_policy_override_endpoint(
    body: SetPolicyOverrideRequest, _user: str = Depends(require_session)
) -> Dict[str, Any]:
    from app.outcome_agent.config.policy_overrides import DEFAULT_SCOPE, set_scope_override

    store = CaseStore()
    try:
        value = await set_scope_override(DEFAULT_SCOPE, body.field, body.value, db_path=store.db_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"scope": "default", "field": body.field, "value": value}


@router.delete("/agent/policy/default/{field}")
async def clear_default_policy_override_endpoint(
    field: str, _user: str = Depends(require_session)
) -> Dict[str, Any]:
    from app.outcome_agent.config.policy_overrides import DEFAULT_SCOPE, clear_scope_override

    store = CaseStore()
    await clear_scope_override(DEFAULT_SCOPE, field, db_path=store.db_path)
    return {"scope": "default", "field": field, "cleared": True}


@router.put("/agent/policy/project/{project_number}")
async def set_project_policy_override_endpoint(
    project_number: str, body: SetPolicyOverrideRequest, _user: str = Depends(require_session)
) -> Dict[str, Any]:
    from app.outcome_agent.config.policy_overrides import project_scope, set_scope_override

    store = CaseStore()
    try:
        value = await set_scope_override(project_scope(project_number), body.field, body.value, db_path=store.db_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"scope": project_scope(project_number), "field": body.field, "value": value}


@router.delete("/agent/policy/project/{project_number}/{field}")
async def clear_project_policy_override_endpoint(
    project_number: str, field: str, _user: str = Depends(require_session)
) -> Dict[str, Any]:
    from app.outcome_agent.config.policy_overrides import clear_scope_override, project_scope

    store = CaseStore()
    await clear_scope_override(project_scope(project_number), field, db_path=store.db_path)
    return {"scope": project_scope(project_number), "field": field, "cleared": True}


class SetRuntimeFlagRequest(BaseModel):
    enabled: bool


@router.post("/runtime-flags/{flag}")
async def set_runtime_flag_endpoint(
    flag: str,
    body: SetRuntimeFlagRequest,
    _user: str = Depends(require_session),
) -> Dict[str, Any]:
    """Agent Policy tab's live On/Off toggles for the two runtime-safe
    flags (AI composer, smart escalation) -- see runtime_flags.py for why
    only these two are toggleable here and not the rest of CHASE_*."""
    from app.services import runtime_flags

    if flag not in runtime_flags.OVERRIDABLE_FLAGS:
        raise HTTPException(status_code=404, detail=f"{flag} is not a runtime-togglable flag.")
    await runtime_flags.set_override(flag, body.enabled)
    return {"flag": flag, "enabled": body.enabled}


@router.get("/policy-documents")
async def get_policy_documents_endpoint(_user: str = Depends(require_session)) -> List[Dict[str, Any]]:
    """Static Knowledge/RAG layer — policy library used when drafting."""
    from app.outcome_agent.config.policies import policy_from_settings
    from app.services.policy_knowledge import list_documents

    config = policy_from_settings(get_settings())
    return [
        {"id": d.id, "title": d.title, "category": d.category, "text": d.text}
        for d in list_documents(config)
    ]


@router.get("/outbox")
async def get_outbox_endpoint(_user: str = Depends(require_session)) -> List[Dict[str, Any]]:
    """Outbox from outcome-agent CaseStore (dry-run and sent)."""
    store = CaseStore()
    rows = await store.list_outbox()
    out = []
    for r in rows:
        case = await store.get(r["case_row_id"])
        out.append(
            {
                "id": r["id"],
                "chase_id": r["case_row_id"],
                "case_row_id": r["case_row_id"],
                "at": r["at"],
                "invoice_no": (case or {}).get("invoice_no"),
                "case_key": (case or {}).get("case_key"),
                "case_id": (case or {}).get("case_id"),
                "project_number": (case or {}).get("project_number"),
                "recipient": r.get("recipient"),
                "subject": r.get("subject"),
                "body": r.get("body"),
                "channel": r.get("channel"),
                "composed": False,
                "requires_human_review": False,
                "evaluation_failures": [],
                "policy_blocked": False,
                "policy_reason": None,
            }
        )
    return out
