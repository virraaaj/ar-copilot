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
from app.agent.tools_read import list_invoices as tool_list_invoices
from app.config import get_settings
from app.documents.index import get_index
from app.documents.ingest import chunk_pages, extract_native_text
from app.documents.sources.manual_upload import ManualUploadSource
from app.services.azure_openai import get_llm
from app.services.backend_client import BackendClient, BackendError, get_backend_client

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


@router.get("/aging-summary")
async def aging_summary_endpoint(
    business_unit_id: Optional[str] = None,
    _user: str = Depends(require_session),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    return await tool_aging_summary(backend, business_unit_id=business_unit_id)


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
        result = await loop.run(body.message, role="viewer", history=history, on_tool_call=collect_tool_call)
        for e in events:
            yield e
        yield _sse_event("answer", {"content": result.answer, "truncated": result.truncated})

    return StreamingResponse(stream(), media_type="text/event-stream")
