"""
Manual upload source — documents live on local disk under
DOCUMENTS_LOCAL_DIR. No external service, no credentials — this is the
source that works with zero setup, and the one the web UI's Documents page
(Phase 1B, Phase 2) uploads through directly.

Storage is one subdirectory per project (added 2026-07-23, project-folder
restructuring): DOCUMENTS_LOCAL_DIR/<project_number>/<filename>. Documents
uploaded before this existed, or with no project_number supplied, land under
a synthetic "_unfiled" folder instead of forcing a migration. The metadata
sidecar is keyed by the project-relative path (not the bare filename) so the
same filename can exist in two different projects' folders without colliding
in either the sidecar or on disk -- filename alone was a real collision risk
under the old flat layout.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from app.config import get_settings
from app.documents.sources.base import DocumentRef, DocumentSource

METADATA_FILENAME = "_metadata.json"
UNFILED_FOLDER = "_unfiled"


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

    async def save(
        self, filename: str, content: bytes, doc_type: Optional[str] = None, project_number: Optional[str] = None
    ) -> DocumentRef:
        """Not part of the DocumentSource read interface (upload is a write)
        — called directly by the upload endpoint/CLI, not by ingestion."""
        folder = project_number or UNFILED_FOLDER
        rel_path = f"{folder}/{filename}"
        dest = self._base / folder / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)

        # Store the resolved folder (never None) so "_unfiled" is filterable
        # like any other project_number value in list_documents() below --
        # DocumentRef itself still reports None for genuinely-unfiled docs
        # (see _folder_to_ref_project below), so that distinction isn't lost
        # on the API surface, only internally.
        meta = self._load_metadata()
        meta[rel_path] = {
            "filename": filename,
            "doc_type": doc_type,
            "size_bytes": len(content),
            "project_number": folder,
        }
        self._save_metadata(meta)

        return DocumentRef(
            source="manual_upload",
            source_id=rel_path,
            filename=filename,
            doc_type=doc_type,
            size_bytes=len(content),
            project_number=project_number,
        )

    @staticmethod
    def _folder_to_ref_project(folder: str) -> Optional[str]:
        return None if folder == UNFILED_FOLDER else folder

    async def list_documents(
        self, doc_type: Optional[str] = None, project_number: Optional[str] = None
    ) -> List[DocumentRef]:
        meta = self._load_metadata()
        refs = []
        for rel_path, info in meta.items():
            if doc_type and info.get("doc_type") != doc_type:
                continue
            if project_number and info.get("project_number") != project_number:
                continue
            refs.append(
                DocumentRef(
                    source="manual_upload",
                    source_id=rel_path,
                    filename=info.get("filename", rel_path),
                    doc_type=info.get("doc_type"),
                    size_bytes=info.get("size_bytes"),
                    project_number=self._folder_to_ref_project(info.get("project_number", UNFILED_FOLDER)),
                )
            )
        return refs

    async def fetch(self, source_id: str) -> bytes:
        path = self._base / source_id
        if not path.exists():
            raise FileNotFoundError(f"No such uploaded document: {source_id}")
        return path.read_bytes()
