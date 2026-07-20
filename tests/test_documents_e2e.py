"""
Phase 1B end-to-end test: real PDF -> real extraction -> real chunking ->
real FTS5 index -> real registry -> agent loop (LLM scripted, since there's
no real key yet). This is the closest thing to PLAN.md §5 Phase 1B's
acceptance criteria ("upload a manual, ask a question only answerable from
it, it answers correctly and cites the source") achievable without a live
model -- everything except the model's own reasoning is real.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fpdf import FPDF

from app.agent.loop import AgentLoop
from app.agent.registry import ToolKind, ToolRegistry
from app.agent import tools_documents
from app.documents.ingest import chunk_pages, extract_native_text
from app.documents.index import LocalKeywordIndex
from app.documents.sources.manual_upload import ManualUploadSource
from app.services.backend_client import BackendClient


def make_tool_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def make_message(content=None, tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat(self, messages, tools=None, tool_choice="auto"):
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_upload_ingest_search_and_answer_with_citation(tmp_path, monkeypatch):
    # ---- 1. A real PDF, with content only findable inside it ----------------
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    pdf.add_page()
    pdf.multi_cell(0, 10, "Cover page. See specifications on the next page.")
    pdf.add_page()
    pdf.multi_cell(0, 10, "Hydraulic pump model HP-4400 requires 5W-30 oil, changed every 500 operating hours.")
    pdf_bytes = bytes(pdf.output())

    # ---- 2. Upload through the manual-upload source --------------------------
    source = ManualUploadSource(base_dir=str(tmp_path / "docs"))
    ref = await source.save("pump_manual.pdf", pdf_bytes, doc_type="equipment_manual")

    # ---- 3. Ingest: extract + chunk, then index -------------------------------
    pages = extract_native_text(pdf_bytes)
    chunks = chunk_pages(pages)
    index = LocalKeywordIndex(db_path=str(tmp_path / "index.db"))
    await index.add_document(ref.source_id, ref.filename, ref.doc_type, chunks)

    monkeypatch.setattr(tools_documents, "get_index", lambda: index)

    # ---- 4. Registry with just the document tools ----------------------------
    registry = ToolRegistry()
    registry.register_many(ToolKind.READ, tools_documents.SCHEMAS, tools_documents.HANDLERS)

    # ---- 5. Scripted agent loop: search, then answer with a citation ---------
    llm = ScriptedLLM(
        [
            make_message(tool_calls=[make_tool_call("t1", "search_documents", {"query": "hydraulic pump oil change interval", "doc_type": "equipment_manual"})]),
            make_message(content="Per pump_manual.pdf (page 2): the HP-4400 needs 5W-30 oil, changed every 500 operating hours."),
        ]
    )
    backend = BackendClient("http://unused", "a@b.com", "x")  # document tools ignore this
    loop = AgentLoop(llm=llm, registry=registry, backend_client=backend)

    result = await loop.run("How often does the hydraulic pump need an oil change, and with what oil?")

    assert "5W-30" in result.answer
    assert "500" in result.answer
    assert "pump_manual.pdf" in result.answer  # cites the source document
    assert "page 2" in result.answer  # cites the page

    # The tool actually ran and found the real indexed content -- not a fake.
    assert len(result.tool_calls) == 1
    search_result = result.tool_calls[0].result
    assert search_result[0]["filename"] == "pump_manual.pdf"
    assert search_result[0]["page"] == 2
    assert "5W-30" in search_result[0]["excerpt"]

    await backend.close()


@pytest.mark.asyncio
async def test_registry_has_all_phase_1_and_1b_tools():
    from app.agent.setup import build_registry

    registry = build_registry()
    tool_names = {t.name for t in registry.for_role("viewer")}

    assert tool_names == {
        "list_invoices", "get_invoice", "get_timeline", "get_project_contacts",
        "list_review_tasks", "aging_summary", "get_project_digest", "get_chase_status",
        "search_documents", "get_document", "list_recent_documents",
    }
    assert all(t.kind is ToolKind.READ for t in registry.for_role("viewer"))
