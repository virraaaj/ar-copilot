"""Tests: chase_evaluator.py (added 2026-07-28, spec §6.14)."""
from __future__ import annotations

from app.services.chase_evaluator import evaluate_message


def _chase(**overrides):
    chase = {"invoice_no": "INV-4821"}
    chase.update(overrides)
    return chase


def test_a_good_message_passes():
    text = "Hi John, following up on invoice INV-4821 -- could you confirm a payment date?"
    result = evaluate_message(text, _chase())
    assert result.passed is True
    assert result.failures == []


def test_missing_invoice_reference_fails():
    text = "Hi John, could you confirm a payment date for the outstanding balance?"
    result = evaluate_message(text, _chase())
    assert result.passed is False
    assert result.checklist["contains_invoice_reference"] is False


def test_no_question_fails():
    text = "Hi John, invoice INV-4821 is overdue."
    result = evaluate_message(text, _chase())
    assert result.passed is False
    assert result.checklist["asks_for_one_concrete_next_step"] is False


def test_banned_language_fails():
    text = "Invoice INV-4821 is overdue -- pay now or we'll pursue legal action, ok?"
    result = evaluate_message(text, _chase())
    assert result.passed is False
    assert result.checklist["avoids_threatening_or_discount_language"] is False


def test_shouting_fails():
    text = "PAY THIS INVOICE INV-4821 IMMEDIATELY, will you?"
    result = evaluate_message(text, _chase())
    assert result.passed is False
    assert result.checklist["preserves_relationship_tone"] is False


def test_too_short_fails():
    text = "INV-4821?"
    result = evaluate_message(text, _chase())
    assert result.passed is False
    assert result.checklist["not_degenerately_short"] is False


def test_no_invoice_number_on_chase_does_not_crash():
    result = evaluate_message("Any update on this?", _chase(invoice_no=None, case_key=None))
    assert result.checklist["contains_invoice_reference"] is False
