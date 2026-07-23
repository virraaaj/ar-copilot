"""
Document index — hybrid text+vector search, backed by Azure AI Search once
provisioned (AZURE_SEARCH_ENDPOINT/KEY — blocked, PLAN.md §7). Until then,
LocalKeywordIndex gives real keyword search today via SQLite FTS5, no Azure
resource needed. Same DocumentIndex interface either way, so get_index()
swapping over later is a config change, not a call-site change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional

import aiosqlite

from app.config import get_settings
from app.documents.ingest import Chunk


@dataclass
class IndexedChunk:
    doc_id: str
    filename: str
    doc_type: Optional[str]
    page_number: int
    chunk_index: int
    text: str
    project_number: Optional[str] = None


@dataclass
class SearchResult:
    chunk: IndexedChunk
    score: float  # higher = more relevant, regardless of backend


class DocumentIndex(ABC):
    @abstractmethod
    async def add_document(
        self, doc_id: str, filename: str, doc_type: Optional[str], chunks: List[Chunk],
        project_number: Optional[str] = None,
    ) -> None: ...

    @abstractmethod
    async def search(
        self, query: str, doc_type: Optional[str] = None, top_k: int = 5, project_number: Optional[str] = None,
    ) -> List[SearchResult]: ...

    @abstractmethod
    async def delete_document(self, doc_id: str) -> None: ...

    @abstractmethod
    async def get_chunks(self, doc_id: str) -> List[IndexedChunk]:
        """All indexed chunks for one document, in page/chunk order —
        for get_document, as distinct from search()'s relevance-ranked
        excerpts."""


def _fts_query(query: str) -> str:
    """Quote each token so punctuation/special FTS5 syntax characters in a
    natural-language question don't break the MATCH query."""
    tokens = [t for t in query.split() if t]
    quoted = [f'"{t}"' for t in tokens]
    return " OR ".join(quoted) if quoted else '""'


class LocalKeywordIndex(DocumentIndex):
    """SQLite FTS5-backed keyword search. Real search, zero external
    dependency — what search_documents actually runs against until
    AZURE_SEARCH_KEY is set."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        s = get_settings()
        self._db_path = db_path or s.STATE_DB_PATH
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            # Unlike regular tables, FTS5 virtual tables reject ALTER TABLE
            # entirely ("virtual tables may not be altered") -- there's no
            # ADD COLUMN migration path like ChaseStore uses for its regular
            # tables. Detect a pre-existing table from before project_number
            # existed and drop+recreate it instead. This is a search index
            # rebuilt from the documents already on disk, not a source of
            # truth -- safe to lose and it'll be empty until the next
            # add_document call re-indexes (fine for this dev tool).
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='document_chunks_fts'"
            )
            exists = await cursor.fetchone()
            if exists:
                cursor = await db.execute("PRAGMA table_info(document_chunks_fts)")
                columns = {row[1] for row in await cursor.fetchall()}
                if "project_number" not in columns:
                    await db.execute("DROP TABLE document_chunks_fts")

            await db.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(
                    doc_id UNINDEXED,
                    filename UNINDEXED,
                    doc_type UNINDEXED,
                    page_number UNINDEXED,
                    chunk_index UNINDEXED,
                    text,
                    project_number UNINDEXED
                )
                """
            )
            await db.commit()
        self._initialized = True

    async def add_document(
        self, doc_id: str, filename: str, doc_type: Optional[str], chunks: List[Chunk],
        project_number: Optional[str] = None,
    ) -> None:
        await self._ensure_schema()
        await self.delete_document(doc_id)  # re-indexing replaces, never duplicates
        if not chunks:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.executemany(
                "INSERT INTO document_chunks_fts (doc_id, filename, doc_type, page_number, chunk_index, text, project_number) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(doc_id, filename, doc_type, c.page_number, c.chunk_index, c.text, project_number) for c in chunks],
            )
            await db.commit()

    async def search(
        self, query: str, doc_type: Optional[str] = None, top_k: int = 5, project_number: Optional[str] = None,
    ) -> List[SearchResult]:
        await self._ensure_schema()
        sql = (
            "SELECT doc_id, filename, doc_type, page_number, chunk_index, text, project_number, "
            "bm25(document_chunks_fts) AS rank FROM document_chunks_fts WHERE document_chunks_fts MATCH ?"
        )
        params: List[Any] = [_fts_query(query)]
        if doc_type:
            sql += " AND doc_type = ?"
            params.append(doc_type)
        if project_number:
            sql += " AND project_number = ?"
            params.append(project_number)
        sql += " ORDER BY rank LIMIT ?"
        params.append(top_k)

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()

        results = []
        for row in rows:
            chunk = IndexedChunk(
                doc_id=row["doc_id"],
                filename=row["filename"],
                doc_type=row["doc_type"],
                page_number=row["page_number"],
                chunk_index=row["chunk_index"],
                text=row["text"],
                project_number=row["project_number"],
            )
            # SQLite's bm25() is lower-is-better; negate so higher = more
            # relevant everywhere, matching Azure AI Search's score semantics.
            results.append(SearchResult(chunk=chunk, score=-row["rank"]))
        return results

    async def delete_document(self, doc_id: str) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM document_chunks_fts WHERE doc_id = ?", (doc_id,))
            await db.commit()

    async def get_chunks(self, doc_id: str) -> List[IndexedChunk]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT doc_id, filename, doc_type, page_number, chunk_index, text, project_number FROM document_chunks_fts "
                "WHERE doc_id = ? ORDER BY page_number, chunk_index",
                (doc_id,),
            )
            rows = await cursor.fetchall()
        return [
            IndexedChunk(
                doc_id=row["doc_id"], filename=row["filename"], doc_type=row["doc_type"],
                page_number=row["page_number"], chunk_index=row["chunk_index"], text=row["text"],
                project_number=row["project_number"],
            )
            for row in rows
        ]


class AzureSearchIndex(DocumentIndex):
    """Hybrid text+vector index via Azure AI Search. Blocked — no
    AZURE_SEARCH_ENDPOINT/KEY provisioned yet, and vector search also needs
    Azure OpenAI embeddings (same missing key as Phase 1's chat model). The
    azure-search-documents SDK is already in requirements.txt; only the
    resource + keys are missing."""

    def __init__(self) -> None:
        s = get_settings()
        if not s.AZURE_SEARCH_ENDPOINT or not s.AZURE_SEARCH_KEY:
            raise RuntimeError(
                "AzureSearchIndex requires AZURE_SEARCH_ENDPOINT and AZURE_SEARCH_KEY. "
                "Use LocalKeywordIndex (the default via get_index()) until both are set."
            )
        raise NotImplementedError("AzureSearchIndex: implement once AZURE_SEARCH_* is provisioned.")

    # ABCMeta checks abstractness (and refuses to instantiate) BEFORE
    # __init__ runs at all -- these stubs exist purely so the class is
    # concrete enough to reach the __init__ guard above. Unreachable in
    # practice: __init__ always raises first.
    async def add_document(self, doc_id, filename, doc_type, chunks, project_number=None) -> None:
        raise NotImplementedError

    async def search(self, query, doc_type=None, top_k=5, project_number=None):
        raise NotImplementedError

    async def delete_document(self, doc_id: str) -> None:
        raise NotImplementedError

    async def get_chunks(self, doc_id: str):
        raise NotImplementedError


_index: Optional[DocumentIndex] = None


def get_index() -> DocumentIndex:
    """LocalKeywordIndex today; swaps to AzureSearchIndex automatically once
    AZURE_SEARCH_ENDPOINT/KEY are both set — no call site changes."""
    global _index
    if _index is None:
        s = get_settings()
        _index = AzureSearchIndex() if (s.AZURE_SEARCH_ENDPOINT and s.AZURE_SEARCH_KEY) else LocalKeywordIndex()
    return _index
