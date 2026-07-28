"""
Runtime-togglable feature flags (added 2026-07-28) -- lets an operator
flip CHASE_COMPOSER_ENABLED / CHASE_SMART_ESCALATION_ENABLED from the
Agent Policy UI without editing .env and restarting the process.

Every other CHASE_* setting stays exactly as it was: a plain env-backed
Settings field, read once at process start. These two are singled out
because they're the cheap, reversible, no-restart-required ones an
operator would plausibly want to try live during a demo; the rest
(CHASE_ENABLED, CHASE_DRY_RUN, allowlists, ...) are deliberately left as
deploy-time decisions, not something a UI click should be able to change.

Same persistence pattern as sim_clock.py: one row per flag in the same
STATE_DB_PATH SQLite file, so an override survives a server restart. No
row for a flag means "no override" -- the real Settings value applies,
same as a fresh install.
"""
from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from app.config import get_settings

# Whitelist of flags this module will actually store/apply an override
# for -- set_override() rejects anything else so the API surface built on
# top of this can never be used to override arbitrary settings.
OVERRIDABLE_FLAGS = {"CHASE_COMPOSER_ENABLED", "CHASE_SMART_ESCALATION_ENABLED"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_flag_overrides (
    flag TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""


def _db_path(db_path: Optional[str] = None) -> str:
    return db_path or get_settings().STATE_DB_PATH


async def _ensure_schema(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


async def get_override(flag: str, db_path: Optional[str] = None) -> Optional[bool]:
    path = _db_path(db_path)
    await _ensure_schema(path)
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute("SELECT value FROM runtime_flag_overrides WHERE flag = ?", (flag,))
        row = await cursor.fetchone()
    return bool(row[0]) if row else None


async def set_override(flag: str, value: bool, db_path: Optional[str] = None) -> None:
    if flag not in OVERRIDABLE_FLAGS:
        raise ValueError(f"{flag} is not a runtime-togglable flag")
    path = _db_path(db_path)
    await _ensure_schema(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO runtime_flag_overrides (flag, value) VALUES (?, ?) "
            "ON CONFLICT(flag) DO UPDATE SET value = excluded.value",
            (flag, int(value)),
        )
        await db.commit()


async def effective(flag: str, settings: Any, db_path: Optional[str] = None) -> bool:
    """The value chase_engine.py should actually act on: the operator's
    override if one has been set, else whatever .env/Settings says --
    identical fallback behavior to a flag nobody has ever touched."""
    override = await get_override(flag, db_path)
    if override is not None:
        return override
    return bool(getattr(settings, flag, False))
