"""
PDF ingestion: extract text (native layer first, OCR fallback for scans),
then chunk per page for embedding + indexing.

Native extraction (pypdf) needs no credentials and works today. OCR fallback
needs Azure AI Document Intelligence (AZURE_DOCUMENT_INTELLIGENCE_KEY) — not
yet provisioned (PLAN.md §7). Structured so that arrives as one class
(DocumentIntelligenceOcrClient) implementing a one-method protocol; nothing
else in this module changes when it's wired in.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import List, Optional, Protocol

import pypdf

logger = logging.getLogger(__name__)

CHUNK_SIZE_CHARS = 1800
CHUNK_OVERLAP_CHARS = 200


@dataclass
class PageText:
    page_number: int
    text: str
    used_ocr: bool = False


@dataclass
class Chunk:
    text: str
    page_number: int
    chunk_index: int


class OcrClient(Protocol):
    """What ingest.py needs from an OCR provider. Azure AI Document
    Intelligence will implement this once provisioned."""

    async def extract_pages(self, pdf_bytes: bytes, page_numbers: List[int]) -> List[PageText]: ...


def extract_native_text(pdf_bytes: bytes) -> List[PageText]:
    """Native (non-OCR) text extraction. Pages with no extractable text come
    back with text="" — the caller decides whether to OCR-fallback them."""
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        pages.append(PageText(page_number=i + 1, text=text))
    return pages


async def extract_with_ocr_fallback(pdf_bytes: bytes, ocr_client: Optional[OcrClient] = None) -> List[PageText]:
    """Native extraction first; any page with no text goes through OCR if a
    client is provided. Without one, scanned pages come back empty and
    flagged via used_ocr=False + empty text — never silently dropped, so a
    caller can tell "this page has nothing" apart from "this page was never
    tried"."""
    pages = extract_native_text(pdf_bytes)
    empty_pages = [p for p in pages if not p.text]
    if not empty_pages:
        return pages

    if ocr_client is None:
        logger.warning(
            "%d page(s) have no extractable text and no OCR client is "
            "configured (AZURE_DOCUMENT_INTELLIGENCE_KEY missing) -- "
            "returning them empty.",
            len(empty_pages),
        )
        return pages

    ocr_results = await ocr_client.extract_pages(pdf_bytes, [p.page_number for p in empty_pages])
    ocr_by_page = {p.page_number: p for p in ocr_results}

    return [ocr_by_page.get(p.page_number, p) for p in pages]


def chunk_pages(pages: List[PageText], chunk_size: int = CHUNK_SIZE_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> List[Chunk]:
    """Fixed-size sliding-window chunking, per page — a chunk never spans
    pages, so page citations stay accurate."""
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    chunks: List[Chunk] = []
    for page in pages:
        text = page.text
        if not text:
            continue
        start = 0
        idx = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunks.append(Chunk(text=text[start:end], page_number=page.page_number, chunk_index=idx))
            idx += 1
            if end == len(text):
                break
            start = end - overlap
    return chunks
