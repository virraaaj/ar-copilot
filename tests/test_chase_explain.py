"""Tests: chase_explain.py (added 2026-07-28, spec §6.19)."""
from __future__ import annotations

from app.services.chase_explain import explain_event


def _chase(**overrides):
    chase = {"invoice_no": "INV-4821", "case_key": "CK-1", "target": "customer"}
    chase.update(overrides)
    return chase


def test_explains_initial_outreach():
    event = {"kind": "outreach_sent", "detail": {"target": "pm", "kind": "outreach"}}
    text = explain_event(_chase(), event)
    assert "overdue" in text
    assert "PM" in text


def test_explains_blocker_with_resolution_date():
    event = {"kind": "blocker_reported", "detail": {
        "blocker_type": "approval_pending", "blocker_resolution_date": "2026-08-01",
    }}
    text = explain_event(_chase(), event)
    assert "approval pending" in text
    assert "2026-08-01" in text


def test_explains_blocker_with_no_resolution_date():
    event = {"kind": "blocker_reported", "detail": {"blocker_type": "cash_flow", "blocker_resolution_date": None}}
    text = explain_event(_chase(), event)
    assert "no resolution date" in text.lower() or "asked when" in text.lower()


def test_explains_commitment_tracked():
    event = {"kind": "commitment_tracked", "detail": {"promised_date": "2026-08-15", "promised_by": "customer"}}
    text = explain_event(_chase(), event)
    assert "2026-08-15" in text


def test_explains_dispute_escalation():
    event = {"kind": "escalated", "detail": {"reason": "dispute"}}
    text = explain_event(_chase(), event)
    assert "dispute" in text.lower()


def test_explains_nudge_budget_escalation():
    event = {"kind": "escalated", "detail": {"reason": "nudge_budget_exhausted"}}
    text = explain_event(_chase(), event)
    assert "no reply" in text.lower() or "follow-up" in text.lower()


def test_explains_unsubscribe_suppression():
    event = {"kind": "suppressed", "detail": {"reason": "unsubscribe", "target": "customer"}}
    text = explain_event(_chase(), event)
    assert "unsubscribed" in text.lower()


def test_explains_reply_with_review_flag():
    event = {"kind": "reply_received", "detail": {"target": "customer", "sentiment": "angry", "requires_human_review": True}}
    text = explain_event(_chase(), event)
    assert "angry" in text.lower()
    assert "flagged" in text.lower()


def test_unrecognized_kind_returns_none():
    event = {"kind": "created", "detail": {}}
    assert explain_event(_chase(), event) is None
