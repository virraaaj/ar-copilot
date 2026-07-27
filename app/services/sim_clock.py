"""
Simulated clock (added 2026-07-25, long-horizon outcome agent spec §6.16).
The demo needs to "advance" days -- show a follow-up date arriving, a
promise getting missed, a payment posting -- in minutes, not by waiting
for real days to pass. Everything that computes dates already goes
through one seam each: chase_machine.py's `now` parameter (threaded into
every function that does date math, defaulting to real time) and
chase_engine.py's couple of direct datetime.now() calls. This module is
the one place that decides what "now" actually is; chase_engine.py reads
it once per tick/request and passes that single value everywhere so a
whole tick agrees on what day it is.

Persisted (not just in-memory) so a simulated date survives a server
restart mid-demo -- same STATE_DB_PATH SQLite file the rest of local
state already lives in. Real wall-clock time is always the default;
nothing here changes behavior until an operator explicitly advances the
clock, and a fresh install (no sim_clock row yet) behaves exactly like
today.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    simulated_at TEXT
);
"""


def _real_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _db_path(db_path: Optional[str] = None) -> str:
    return db_path or get_settings().STATE_DB_PATH


async def _ensure_schema(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def get_simulated_at(db_path: Optional[str] = None) -> Optional[datetime]:
    """The simulated 'now' if an operator has set one, else None (meaning:
    nothing has ever overridden the clock, use real time)."""
    path = _db_path(db_path)
    await _ensure_schema(path)
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute("SELECT simulated_at FROM sim_clock WHERE id = 1")
        row = await cursor.fetchone()
    if row and row[0]:
        return datetime.fromisoformat(row[0])
    return None


async def now(db_path: Optional[str] = None) -> datetime:
    """The clock every date-aware decision in the app should use. Real
    time until an operator advances it via advance_days/set_simulated_at."""
    simulated = await get_simulated_at(db_path)
    return simulated if simulated is not None else _real_now()


async def is_simulated(db_path: Optional[str] = None) -> bool:
    return (await get_simulated_at(db_path)) is not None


async def set_simulated_at(when: datetime, db_path: Optional[str] = None) -> datetime:
    path = _db_path(db_path)
    await _ensure_schema(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO sim_clock (id, simulated_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET simulated_at = excluded.simulated_at",
            (when.isoformat(),),
        )
        await db.commit()
    return when


async def advance_days(days: int, db_path: Optional[str] = None) -> datetime:
    """Moves the clock forward from wherever it currently is (real time,
    if it's never been touched) -- so "advance 3 days" twice in a row
    lands 6 days from the original real start, not 3 days from "now"
    each time."""
    current = await now(db_path)
    new_time = current + timedelta(days=days)
    return await set_simulated_at(new_time, db_path)


async def reset_to_real_time(db_path: Optional[str] = None) -> None:
    """Clears the override -- the clock goes back to tracking the real
    system clock, same as a fresh install that never touched it."""
    path = _db_path(db_path)
    await _ensure_schema(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO sim_clock (id, simulated_at) VALUES (1, NULL) "
            "ON CONFLICT(id) DO UPDATE SET simulated_at = NULL"
        )
        await db.commit()
