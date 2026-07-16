"""Tests: FollowUpStore (added 2026-07-16). Real SQLite against a temp file."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.followup_store import FollowUpError, FollowUpStore


@pytest.fixture
def store(tmp_path) -> FollowUpStore:
    return FollowUpStore(db_path=str(tmp_path / "state.db"))


@pytest.mark.asyncio
async def test_create_then_get_active(store: FollowUpStore) -> None:
    campaign_id = await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)

    active = await store.get_active_for_case("case-1")

    assert active is not None
    assert active["id"] == campaign_id
    assert active["customer_email"] == "customer@example.com"
    assert active["status"] == "active"


@pytest.mark.asyncio
async def test_new_campaign_is_immediately_due(store: FollowUpStore) -> None:
    """"Follow up with the customer" should send the first email right
    away, not wait a full cadence period."""
    await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)

    due = await store.list_due()

    assert len(due) == 1
    assert due[0]["case_id"] == "case-1"


@pytest.mark.asyncio
async def test_cannot_create_second_active_campaign_for_same_case(store: FollowUpStore) -> None:
    await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=3)

    with pytest.raises(FollowUpError):
        await store.create("case-1", "other@example.com", "pm@corehelix.ai", cadence_days=1)


@pytest.mark.asyncio
async def test_mark_sent_advances_next_send_at_by_cadence(store: FollowUpStore) -> None:
    campaign_id = await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=5)
    before = await store.get_active_for_case("case-1")

    await store.mark_sent(campaign_id, "customer@example.com", cadence_days=5, end_date=None)

    after = await store.get_active_for_case("case-1")
    assert after["send_count"] == 1
    before_next = datetime.fromisoformat(before["next_send_at"])
    after_next = datetime.fromisoformat(after["next_send_at"])
    assert (after_next - before_next).days >= 4  # ~5 days later, allowing for test execution time


@pytest.mark.asyncio
async def test_mark_sent_records_send_history(store: FollowUpStore) -> None:
    campaign_id = await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=1)

    await store.mark_sent(campaign_id, "customer@example.com", cadence_days=1, end_date=None)

    history = await store.list_send_history("case-1")
    assert len(history) == 1
    assert history[0]["to_email"] == "customer@example.com"


@pytest.mark.asyncio
async def test_mark_sent_completes_campaign_once_past_end_date(store: FollowUpStore) -> None:
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    campaign_id = await store.create(
        "case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=1, end_date=yesterday
    )

    await store.mark_sent(campaign_id, "customer@example.com", cadence_days=1, end_date=yesterday)

    assert await store.get_active_for_case("case-1") is None  # no longer active


@pytest.mark.asyncio
async def test_cancel_stops_future_sends(store: FollowUpStore) -> None:
    await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=1)

    cancelled = await store.cancel("case-1")

    assert cancelled is True
    assert await store.get_active_for_case("case-1") is None
    assert await store.list_due() == []


@pytest.mark.asyncio
async def test_cancel_unknown_case_returns_false(store: FollowUpStore) -> None:
    assert await store.cancel("no-such-case") is False


@pytest.mark.asyncio
async def test_can_create_new_campaign_after_cancelling_old_one(store: FollowUpStore) -> None:
    await store.create("case-1", "old@example.com", "pm@corehelix.ai", cadence_days=1)
    await store.cancel("case-1")

    new_id = await store.create("case-1", "new@example.com", "pm@corehelix.ai", cadence_days=1)

    active = await store.get_active_for_case("case-1")
    assert active["id"] == new_id
    assert active["customer_email"] == "new@example.com"


@pytest.mark.asyncio
async def test_mark_mirrored_updates_last_mirrored_at(store: FollowUpStore) -> None:
    campaign_id = await store.create("case-1", "customer@example.com", "pm@corehelix.ai", cadence_days=1)

    await store.mark_mirrored(campaign_id, "2026-07-16T12:00:00")

    active = await store.get_active_for_case("case-1")
    assert active["last_mirrored_at"] == "2026-07-16T12:00:00"
