"""Tests: guardrails/policy.py (added 2026-07-16, for the follow-up feature's
email/cadence/end-date validation -- earlier rules were only exercised
indirectly through tools_write.py's tests)."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.guardrails.policy import (
    PolicyViolation,
    check_future_or_today,
    check_positive_int,
    check_valid_email,
)


def test_valid_email_passes():
    check_valid_email("customer@example.com")  # must not raise


def test_missing_email_rejected():
    with pytest.raises(PolicyViolation):
        check_valid_email(None)


def test_malformed_email_rejected():
    with pytest.raises(PolicyViolation):
        check_valid_email("not-an-email")


def test_positive_int_passes():
    check_positive_int(1, "cadence_days")  # must not raise


def test_zero_or_negative_int_rejected():
    with pytest.raises(PolicyViolation):
        check_positive_int(0, "cadence_days")
    with pytest.raises(PolicyViolation):
        check_positive_int(-1, "cadence_days")


def test_future_date_passes():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    check_future_or_today(tomorrow, "end_date")  # must not raise


def test_today_passes():
    check_future_or_today(date.today().isoformat(), "end_date")  # must not raise


def test_past_date_rejected():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with pytest.raises(PolicyViolation):
        check_future_or_today(yesterday, "end_date")


def test_none_date_is_allowed_optional_field():
    check_future_or_today(None, "end_date")  # must not raise -- no end date is valid


def test_malformed_date_rejected():
    with pytest.raises(PolicyViolation):
        check_future_or_today("not-a-date", "end_date")
