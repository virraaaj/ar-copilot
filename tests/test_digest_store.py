"""Tests: DigestStore (added 2026-07-17). Real SQLite against a temp file."""
from __future__ import annotations

from datetime import date

import pytest

from app.services.digest_store import DigestStore, current_period_key


def test_current_period_key_format():
    key = current_period_key(date(2026, 7, 17))  # a Friday in ISO week 29 of 2026
    assert key == "2026-W29"


def test_current_period_key_is_stable_within_the_same_week():
    monday = current_period_key(date(2026, 7, 13))
    friday = current_period_key(date(2026, 7, 17))
    assert monday == friday


@pytest.fixture
def store(tmp_path) -> DigestStore:
    return DigestStore(db_path=str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_get_last_snapshot_none_when_never_sent(store: DigestStore) -> None:
    assert await store.get_last_snapshot("PN-1") is None


@pytest.mark.asyncio
async def test_record_then_get_snapshot(store: DigestStore) -> None:
    await store.record_snapshot("PN-1", "2026-W29", total_open_amount=50000.0, open_invoice_count=3, overdue_count=1)

    snapshot = await store.get_last_snapshot("PN-1")

    assert snapshot["period_key"] == "2026-W29"
    assert snapshot["total_open_amount"] == 50000.0
    assert snapshot["open_invoice_count"] == 3
    assert snapshot["overdue_count"] == 1


@pytest.mark.asyncio
async def test_already_sent_this_period(store: DigestStore) -> None:
    await store.record_snapshot("PN-1", "2026-W29", total_open_amount=0, open_invoice_count=0, overdue_count=0)

    assert await store.already_sent_this_period("PN-1", "2026-W29") is True
    assert await store.already_sent_this_period("PN-1", "2026-W30") is False


@pytest.mark.asyncio
async def test_not_sent_when_never_recorded(store: DigestStore) -> None:
    assert await store.already_sent_this_period("PN-unknown", "2026-W29") is False


@pytest.mark.asyncio
async def test_recording_a_new_period_overwrites_the_snapshot(store: DigestStore) -> None:
    await store.record_snapshot("PN-1", "2026-W29", total_open_amount=50000.0, open_invoice_count=3, overdue_count=1)
    await store.record_snapshot("PN-1", "2026-W30", total_open_amount=62000.0, open_invoice_count=4, overdue_count=2)

    snapshot = await store.get_last_snapshot("PN-1")

    assert snapshot["period_key"] == "2026-W30"
    assert snapshot["total_open_amount"] == 62000.0
