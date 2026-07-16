"""
Phase 1B tests: LocalKeywordIndex (SQLite FTS5). Real search against a real
temp database — no mocking, since this has no external dependency to mock.
"""
from __future__ import annotations

import pytest

from app.documents.index import AzureSearchIndex, LocalKeywordIndex
from app.documents.ingest import Chunk


@pytest.fixture
async def index(tmp_path) -> LocalKeywordIndex:
    return LocalKeywordIndex(db_path=str(tmp_path / "test.db"))


def test_azure_search_index_raises_clear_error_without_credentials(monkeypatch) -> None:
    """Regression test: ABCMeta checks abstractness before __init__ runs, so
    an incompletely-stubbed subclass raises a confusing TypeError instead of
    this class's own clear RuntimeError unless every abstract method has a
    (possibly-unreachable) concrete override."""
    monkeypatch.setenv("AZURE_SEARCH_ENDPOINT", "")
    monkeypatch.setenv("AZURE_SEARCH_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    with pytest.raises(RuntimeError, match="AZURE_SEARCH_ENDPOINT"):
        AzureSearchIndex()


@pytest.mark.asyncio
async def test_add_then_search_finds_relevant_chunk(index: LocalKeywordIndex) -> None:
    chunks = [
        Chunk(text="The hydraulic pump requires 5W-30 oil, changed every 500 hours.", page_number=1, chunk_index=0),
        Chunk(text="Warranty coverage is void if the seal is tampered with.", page_number=2, chunk_index=0),
    ]
    await index.add_document("doc-1", "pump_manual.pdf", "equipment_manual", chunks)

    results = await index.search("hydraulic pump oil")

    assert len(results) >= 1
    assert "hydraulic pump" in results[0].chunk.text
    assert results[0].chunk.filename == "pump_manual.pdf"


@pytest.mark.asyncio
async def test_search_filters_by_doc_type(index: LocalKeywordIndex) -> None:
    await index.add_document(
        "doc-1", "manual.pdf", "equipment_manual",
        [Chunk(text="torque specification is 45 Nm", page_number=1, chunk_index=0)],
    )
    await index.add_document(
        "doc-2", "cert.pdf", "vendor_certification",
        [Chunk(text="torque wrench certified annually", page_number=1, chunk_index=0)],
    )

    manual_results = await index.search("torque", doc_type="equipment_manual")

    assert len(manual_results) == 1
    assert manual_results[0].chunk.filename == "manual.pdf"


@pytest.mark.asyncio
async def test_reindexing_document_replaces_not_duplicates(index: LocalKeywordIndex) -> None:
    await index.add_document("doc-1", "v1.pdf", None, [Chunk(text="old content", page_number=1, chunk_index=0)])
    await index.add_document("doc-1", "v2.pdf", None, [Chunk(text="new content", page_number=1, chunk_index=0)])

    results = await index.search("content")

    assert len(results) == 1
    assert results[0].chunk.filename == "v2.pdf"


@pytest.mark.asyncio
async def test_delete_document_removes_its_chunks(index: LocalKeywordIndex) -> None:
    await index.add_document("doc-1", "gone.pdf", None, [Chunk(text="findable text", page_number=1, chunk_index=0)])

    await index.delete_document("doc-1")
    results = await index.search("findable")

    assert results == []


@pytest.mark.asyncio
async def test_query_with_punctuation_does_not_crash(index: LocalKeywordIndex) -> None:
    await index.add_document("doc-1", "doc.pdf", None, [Chunk(text="net 30 payment terms apply", page_number=1, chunk_index=0)])

    # FTS5 treats bare *, ", -, ( etc. as query syntax -- a natural-language
    # question shouldn't be able to break the MATCH query.
    results = await index.search('what are the payment terms? (net-30 or "COD"?)')

    assert isinstance(results, list)  # doesn't raise


@pytest.mark.asyncio
async def test_no_match_returns_empty_list_not_error(index: LocalKeywordIndex) -> None:
    await index.add_document("doc-1", "doc.pdf", None, [Chunk(text="something else entirely", page_number=1, chunk_index=0)])

    results = await index.search("nonexistent gibberish query terms")

    assert results == []


@pytest.mark.asyncio
async def test_get_chunks_returns_full_document_in_order(index: LocalKeywordIndex) -> None:
    chunks = [
        Chunk(text="page 2 chunk 1", page_number=2, chunk_index=1),
        Chunk(text="page 1 chunk 0", page_number=1, chunk_index=0),
        Chunk(text="page 2 chunk 0", page_number=2, chunk_index=0),
    ]
    await index.add_document("doc-1", "doc.pdf", "equipment_manual", chunks)

    result = await index.get_chunks("doc-1")

    assert [(c.page_number, c.chunk_index) for c in result] == [(1, 0), (2, 0), (2, 1)]
