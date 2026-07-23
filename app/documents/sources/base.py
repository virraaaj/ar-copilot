"""
DocumentSource interface — pluggable storage backends for Document
Intelligence (PLAN.md §5 Phase 1B). Every source, present or future,
implements this; ingestion (ingest.py) is source-agnostic once it has bytes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DocumentRef:
    """A reference to a document at its source, before ingestion."""

    source: str  # "sharepoint" | "manual_upload"
    source_id: str  # opaque id within that source (drive item id, local filename, ...)
    filename: str
    doc_type: Optional[str] = None  # vendor_certification | customer_manual | quote | terms_and_conditions | equipment_manual
    size_bytes: Optional[int] = None
    modified_at: Optional[str] = None  # ISO timestamp, if the source has one
    # Which project this document belongs to (added 2026-07-23, project-folder
    # restructuring). None means "unfiled" -- pre-migration uploads, or a
    # source (SharePoint) that doesn't carry project info at all.
    project_number: Optional[str] = None


class DocumentSource(ABC):
    """A place documents can come from. Adding a new storage integration
    later (network drive, other DMS) means a new class here, not a rewrite
    of ingestion/search."""

    @abstractmethod
    async def list_documents(self, doc_type: Optional[str] = None) -> List[DocumentRef]:
        """Enumerate what's available, optionally filtered by doc_type."""

    @abstractmethod
    async def fetch(self, source_id: str) -> bytes:
        """Raw bytes for one document, by its source_id."""
