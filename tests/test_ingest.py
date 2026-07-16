"""
Phase 1B tests: PDF ingestion. Uses fpdf2 to generate real PDFs in-memory —
no external service or credential needed for native extraction/chunking.
"""
from __future__ import annotations

import io

import pytest
from fpdf import FPDF

from app.documents.ingest import (
    Chunk,
    PageText,
    chunk_pages,
    extract_native_text,
    extract_with_ocr_fallback,
)


def make_text_pdf(pages_text: list[str]) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    for text in pages_text:
        pdf.add_page()
        pdf.multi_cell(0, 10, text)
    return bytes(pdf.output())


def make_blank_pdf(num_pages: int = 1) -> bytes:
    """A PDF with pages that have no text content at all -- stands in for a
    scanned page (pypdf extracts "" from it either way)."""
    pdf = FPDF()
    for _ in range(num_pages):
        pdf.add_page()
        pdf.rect(10, 10, 50, 50)  # a drawn shape, no text objects
    return bytes(pdf.output())


def test_extract_native_text_pulls_real_text():
    pdf_bytes = make_text_pdf(["Hello from page one.", "Second page content here."])

    pages = extract_native_text(pdf_bytes)

    assert len(pages) == 2
    assert "Hello from page one" in pages[0].text
    assert pages[0].page_number == 1
    assert "Second page content" in pages[1].text
    assert pages[1].page_number == 2


def test_extract_native_text_blank_page_returns_empty_not_error():
    pdf_bytes = make_blank_pdf(1)

    pages = extract_native_text(pdf_bytes)

    assert len(pages) == 1
    assert pages[0].text == ""


@pytest.mark.asyncio
async def test_ocr_fallback_without_client_leaves_empty_pages_flagged():
    pdf_bytes = make_blank_pdf(1)

    pages = await extract_with_ocr_fallback(pdf_bytes, ocr_client=None)

    assert len(pages) == 1
    assert pages[0].text == ""
    assert pages[0].used_ocr is False


@pytest.mark.asyncio
async def test_ocr_fallback_with_client_fills_empty_pages():
    pdf_bytes = make_blank_pdf(1)

    class FakeOcrClient:
        async def extract_pages(self, pdf_bytes: bytes, page_numbers: list[int]):
            return [PageText(page_number=n, text="OCR'd text", used_ocr=True) for n in page_numbers]

    pages = await extract_with_ocr_fallback(pdf_bytes, ocr_client=FakeOcrClient())

    assert pages[0].text == "OCR'd text"
    assert pages[0].used_ocr is True


@pytest.mark.asyncio
async def test_ocr_fallback_only_calls_ocr_for_empty_pages():
    pdf_bytes = make_text_pdf(["Has real text already."])

    class FailIfCalled:
        async def extract_pages(self, pdf_bytes: bytes, page_numbers: list[int]):
            raise AssertionError("OCR should not be called for a page that already has text")

    pages = await extract_with_ocr_fallback(pdf_bytes, ocr_client=FailIfCalled())

    assert "Has real text already" in pages[0].text


def test_chunk_pages_splits_long_text_with_overlap():
    long_text = "x" * 5000
    pages = [PageText(page_number=1, text=long_text)]

    chunks = chunk_pages(pages, chunk_size=1800, overlap=200)

    assert len(chunks) > 1
    assert all(c.page_number == 1 for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    # consecutive chunks overlap
    assert chunks[0].text[-200:] == chunks[1].text[:200]


def test_chunk_pages_skips_empty_pages():
    pages = [PageText(page_number=1, text=""), PageText(page_number=2, text="real content")]

    chunks = chunk_pages(pages)

    assert len(chunks) == 1
    assert chunks[0].page_number == 2


def test_chunk_pages_never_spans_pages():
    pages = [PageText(page_number=1, text="a" * 1000), PageText(page_number=2, text="b" * 1000)]

    chunks = chunk_pages(pages, chunk_size=1800, overlap=200)

    page_numbers = {c.page_number for c in chunks}
    assert page_numbers == {1, 2}
    assert all(set(c.text) <= {"a"} for c in chunks if c.page_number == 1)
    assert all(set(c.text) <= {"b"} for c in chunks if c.page_number == 2)


def test_chunk_pages_rejects_overlap_ge_chunk_size():
    with pytest.raises(ValueError):
        chunk_pages([PageText(page_number=1, text="hello")], chunk_size=100, overlap=100)
