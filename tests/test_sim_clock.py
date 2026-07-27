"""Tests: sim_clock.py (added 2026-07-25) -- the demo's simulated clock.
Real SQLite against a temp file, same pattern as test_chase_store.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import sim_clock


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "clock.db")


def _real_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_fresh_clock_has_no_simulated_time(db_path: str) -> None:
    assert await sim_clock.get_simulated_at(db_path) is None
    assert await sim_clock.is_simulated(db_path) is False


@pytest.mark.asyncio
async def test_fresh_clock_now_returns_real_time(db_path: str) -> None:
    before = _real_now()
    result = await sim_clock.now(db_path)
    after = _real_now()

    assert before <= result <= after


@pytest.mark.asyncio
async def test_set_simulated_at_overrides_now(db_path: str) -> None:
    target = datetime(2026, 8, 1, 12, 0, 0)

    await sim_clock.set_simulated_at(target, db_path)

    assert await sim_clock.now(db_path) == target
    assert await sim_clock.is_simulated(db_path) is True


@pytest.mark.asyncio
async def test_advance_days_moves_forward_from_current_simulated_time(db_path: str) -> None:
    await sim_clock.set_simulated_at(datetime(2026, 7, 22), db_path)

    result = await sim_clock.advance_days(3, db_path)

    assert result == datetime(2026, 7, 25)
    assert await sim_clock.now(db_path) == datetime(2026, 7, 25)


@pytest.mark.asyncio
async def test_advance_days_compounds_across_calls(db_path: str) -> None:
    await sim_clock.set_simulated_at(datetime(2026, 7, 22), db_path)
    await sim_clock.advance_days(3, db_path)

    result = await sim_clock.advance_days(4, db_path)

    assert result == datetime(2026, 7, 29)


@pytest.mark.asyncio
async def test_advance_days_from_fresh_clock_starts_from_real_time(db_path: str) -> None:
    before = _real_now()

    result = await sim_clock.advance_days(7, db_path)

    assert timedelta(days=7) <= (result - before) <= timedelta(days=7, minutes=1)


@pytest.mark.asyncio
async def test_reset_to_real_time_clears_the_override(db_path: str) -> None:
    await sim_clock.set_simulated_at(datetime(2026, 8, 1), db_path)

    await sim_clock.reset_to_real_time(db_path)

    assert await sim_clock.get_simulated_at(db_path) is None
    assert await sim_clock.is_simulated(db_path) is False
