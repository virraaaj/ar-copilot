"""
Read-only document tools (PLAN.md §5 Phase 1B). Search runs against
LocalKeywordIndex today, Azure AI Search once provisioned — get_index()
picks automatically, so nothing here changes when that key arrives.

Signature note: the agent loop always injects one positional client arg into
every tool handler (see loop.py / tools_read.py, where it's the Lummus
BackendClient). These tools don't talk to the Lummus backend at all — they
use their own module-level singletons (get_index(), ManualUploadSource()) —
so that first arg is accepted but unused. A shared ToolContext that only
hands each tool what it actually needs would be cleaner; deferred rather
than reworking the already-tested Phase 1 loop for this.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.documents.index import get_index
from app.documents.sources.manual_upload import ManualUploadSource


async def search_documents(
    _backend: Any, query: str, doc_type: Optional[str] = None, top_k: int = 5, project_number: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Search across ingested documents by keyword/description. Returns
    ranked excerpts with page numbers for citation."""
    index = get_index()
    results = await index.search(query, doc_type=doc_type, top_k=top_k, project_number=project_number)
    return [
        {
            "doc_id": r.chunk.doc_id,
            "filename": r.chunk.filename,
            "doc_type": r.chunk.doc_type,
            "page": r.chunk.page_number,
            "excerpt": r.chunk.text[:500],
            "score": round(r.score, 3),
        }
        for r in results
    ]


async def get_document(_backend: Any, doc_id: str) -> Dict[str, Any]:
    """Full indexed text of one document, in page order — use after
    search_documents has identified which document to read in full."""
    index = get_index()
    chunks = await index.get_chunks(doc_id)
    if not chunks:
        return {"error": f"No indexed document with id '{doc_id}'."}
    return {
        "doc_id": doc_id,
        "filename": chunks[0].filename,
        "doc_type": chunks[0].doc_type,
        "page_count": max(c.page_number for c in chunks),
        "full_text": "\n\n".join(f"[page {c.page_number}] {c.text}" for c in chunks),
    }


async def list_recent_documents(
    _backend: Any, doc_type: Optional[str] = None, project_number: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """What's been uploaded, optionally filtered by document type and/or
    project. Sources other than manual upload (e.g. SharePoint) are synced
    in explicitly, not queried live on every call."""
    source = ManualUploadSource()
    refs = await source.list_documents(doc_type=doc_type, project_number=project_number)
    return [
        {"filename": r.filename, "doc_type": r.doc_type, "size_bytes": r.size_bytes, "project_number": r.project_number}
        for r in refs
    ]


SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "search_documents",
        "description": (
            "Search across ingested documents (vendor certifications, customer "
            "manuals, quotes, T&Cs, equipment manuals) by keyword or natural-"
            "language description. Returns ranked excerpts with page numbers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "doc_type": {
                    "type": "string",
                    "enum": ["vendor_certification", "customer_manual", "quote", "terms_and_conditions", "equipment_manual"],
                },
                "top_k": {"type": "integer", "default": 5},
                "project_number": {
                    "type": "string",
                    "description": "Restrict the search to one project's documents. Always pass this when the conversation is scoped to a project.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document",
        "description": "Full text of one specific document, once identified via search_documents.",
        "parameters": {
            "type": "object",
            "properties": {"doc_id": {"type": "string"}},
            "required": ["doc_id"],
        },
    },
    {
        "name": "list_recent_documents",
        "description": "List uploaded/ingested documents, optionally filtered by type and/or project.",
        "parameters": {
            "type": "object",
            "properties": {
                "doc_type": {
                    "type": "string",
                    "enum": ["vendor_certification", "customer_manual", "quote", "terms_and_conditions", "equipment_manual"],
                },
                "project_number": {
                    "type": "string",
                    "description": "Restrict the list to one project's documents. Always pass this when the conversation is scoped to a project.",
                },
            },
        },
    },
]

HANDLERS = {
    "search_documents": search_documents,
    "get_document": get_document,
    "list_recent_documents": list_recent_documents,
}
