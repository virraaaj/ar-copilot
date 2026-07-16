"""Tests: V2-compatible email lineage headers/footer (added 2026-07-16)."""
from __future__ import annotations

from app.services.email_lineage import build_lineage_footer, build_lineage_headers


def test_headers_always_include_engine_marker_and_case_id():
    headers = build_lineage_headers("case-1")

    assert ("X-Dunning-Engine", "v2") in headers
    assert ("X-Dunning-Case-Id", "case-1") in headers


def test_headers_include_optional_fields_when_given():
    headers = build_lineage_headers("case-1", invoice_no="INV-1", stage_code="first_notice")

    assert ("X-Dunning-Invoice-No", "INV-1") in headers
    assert ("X-Dunning-Stage", "first_notice") in headers


def test_headers_stay_within_graphs_five_header_limit():
    headers = build_lineage_headers("case-1", invoice_no="INV-1", stage_code="first_notice")

    assert len(headers) <= 5


def test_footer_matches_the_real_detector_marker_format():
    # Mirrors backend/app/dunning_v2/inbound/detector.py's _FOOTER_MARKER
    # and _token_re: "DUNNING-V2" marker, "key: value" tokens pipe-separated.
    footer = build_lineage_footer("case-1", invoice_no="INV-1", project_number="PN-1", stage_code="first_notice")

    assert "DUNNING-V2" in footer
    assert "case_id: case-1" in footer
    assert "invoice_no: INV-1" in footer
    assert "project_number: PN-1" in footer
    assert "stage_code: first_notice" in footer
    assert 'style="display:none"' in footer


def test_footer_omits_absent_optional_fields():
    footer = build_lineage_footer("case-1")

    assert "case_id: case-1" in footer
    assert "invoice_no" not in footer
    assert "project_number" not in footer
    assert "stage_code" not in footer
