"""Tests: policy_knowledge.py (added 2026-07-28, spec §6.9)."""
from __future__ import annotations

from app.services.chase_machine import ChaseConfig
from app.services.policy_knowledge import list_documents, retrieve


def test_list_documents_returns_all_six_categories():
    docs = list_documents(ChaseConfig())
    categories = {d.category for d in docs}
    assert categories == {"collections", "escalation", "tone", "dispute", "patterns", "frequency"}


def test_documents_reflect_real_config_values():
    config = ChaseConfig(max_nudges=7, nudge_interval_days=5)
    docs = list_documents(config)
    frequency_doc = next(d for d in docs if d.id == "contact-frequency-policy")
    assert "5 days" in frequency_doc.text
    assert "7 nudges" in frequency_doc.text


def test_retrieve_finds_dispute_doc_for_dispute_query():
    results = retrieve("customer says this invoice is wrong and disputes the charge", ChaseConfig())
    assert any(d.id == "dispute-handling-sop" for d in results)


def test_retrieve_finds_frequency_doc_for_nudge_query():
    results = retrieve("nudge follow-up interval between messages", ChaseConfig())
    assert any(d.id == "contact-frequency-policy" for d in results)


def test_retrieve_returns_empty_for_query_with_no_matching_terms():
    results = retrieve("xyzzy plugh qwerty", ChaseConfig())
    assert results == []


def test_retrieve_respects_top_k():
    results = retrieve("invoice payment message customer email policy", ChaseConfig(), top_k=1)
    assert len(results) <= 1
