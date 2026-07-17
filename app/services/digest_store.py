"""
SQLite-backed store behind the weekly AR-health digest cards (added
2026-07-17): remembers each project's last-sent snapshot (so the next
digest can say "open amount up $12K since last week") and dedupes so a
project gets at most one digest per period, same dedup shape as
proactive.py's ReminderDedup.

Period is a plain ISO-week string ("2026-W29") -- coarse on purpose. The
digest is a weekly roll-up, not a per-event notification, so "did we
already send this project's digest for this week" is the only dedup
question that matters.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS digest_snapshots (
    project_number TEXT PRIMARY KEY,
    period_key TEXT NOT NULL,
    total_open_amount REAL NOT NULL,
    open_invoice_count INTEGER NOT NULL,
    overdue_count INTEGER NOT NULL,
    sent_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def current_period_key(today: Optional[date] = None) -> str:
    d = today or date.today()
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


class DigestStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        s = get_settings()
        self._db_path = db_path or s.STATE_DB_PATH
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def get_last_snapshot(self, project_number: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM digest_snapshots WHERE project_number = ?", (project_number,)
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def already_sent_this_period(self, project_number: str, period_key: str) -> bool:
        last = await self.get_last_snapshot(project_number)
        return bool(last) and last["period_key"] == period_key

    async def record_snapshot(
        self,
        project_number: str,
        period_key: str,
        total_open_amount: float,
        open_invoice_count: int,
        overdue_count: int,
    ) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO digest_snapshots "
                "(project_number, period_key, total_open_amount, open_invoice_count, overdue_count, sent_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(project_number) DO UPDATE SET "
                "period_key=excluded.period_key, total_open_amount=excluded.total_open_amount, "
                "open_invoice_count=excluded.open_invoice_count, overdue_count=excluded.overdue_count, "
                "sent_at=excluded.sent_at",
                (project_number, period_key, total_open_amount, open_invoice_count, overdue_count),
            )
            await db.commit()
