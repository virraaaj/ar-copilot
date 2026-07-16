"""
Manual upload source — documents live on local disk under
DOCUMENTS_LOCAL_DIR. No external service, no credentials — this is the
source that works with zero setup, and the one the web UI's Documents page
(Phase 1B, Phase 2) uploads through directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from app.config import get_settings
from app.documents.sources.base import DocumentRef, DocumentSource

METADATA_FILENAME = "_metadata.json"


class ManualUploadSource(DocumentSource):
    def __init__(self, base_dir: Optional[str] = None) -> None:
        s = get_settings()
        self._base = Path(base_dir or s.DOCUMENTS_LOCAL_DIR)
        self._base.mkdir(parents=True, exist_ok=True)

    def _metadata_path(self) -> Path:
        return self._base / METADATA_FILENAME

    def _load_metadata(self) -> Dict[str, dict]:
        p = self._metadata_path()
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))

    def _save_metadata(self, meta: Dict[str, dict]) -> None:
        self._metadata_path().write_text(json.dumps(meta, indent=2), encoding="utf-8")

    async def save(self, filename: str, content: bytes, doc_type: Optional[str] = None) -> DocumentRef:
        """Not part of the DocumentSource read interface (upload is a write)
        — called directly by the upload endpoint/CLI, not by ingestion."""
        dest = self._base / filename
        dest.write_bytes(content)

        meta = self._load_metadata()
        meta[filename] = {"doc_type": doc_type, "size_bytes": len(content)}
        self._save_metadata(meta)

        return DocumentRef(
            source="manual_upload",
            source_id=filename,
            filename=filename,
            doc_type=doc_type,
            size_bytes=len(content),
        )

    async def list_documents(self, doc_type: Optional[str] = None) -> List[DocumentRef]:
        meta = self._load_metadata()
        refs = []
        for filename, info in meta.items():
            if doc_type and info.get("doc_type") != doc_type:
                continue
            refs.append(
                DocumentRef(
                    source="manual_upload",
                    source_id=filename,
                    filename=filename,
                    doc_type=info.get("doc_type"),
                    size_bytes=info.get("size_bytes"),
                )
            )
        return refs

    async def fetch(self, source_id: str) -> bytes:
        path = self._base / source_id
        if not path.exists():
            raise FileNotFoundError(f"No such uploaded document: {source_id}")
        return path.read_bytes()
