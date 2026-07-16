"""
SQLite-backed store for manual follow-up email campaigns (added
2026-07-16). A PM says "follow up with the customer" in Teams (or clicks
Follow up on the web); this is the campaign's home: cadence, end date,
send history for local activity display, and how far replies have been
mirrored into the project's Teams chat.

One active campaign per case, deliberately -- a second "follow up" request
while one's already running is a mistake to catch, not silently overwrite
(see FollowUpError below). The first send happens on the very next poll
(next_send_at is set to "now" at creation), matching what someone asking
to "follow up with the customer" actually expects: an email goes out
now, then repeats.

Send history lives here, not on the real Lummus timeline -- Lummus's
POST /response-events (backend/app/dunning_v2/api/reviews.py) hardcodes
its resulting timeline event to "Inbound reply received"
(TimelineEventType.REPLY_RECEIVED, verified against
backend/app/dunning_v2/reviews/service.py), so using it to log an
*outbound* send would mislabel it. The frontend instead merges this
local send history into the Activity view alongside the real Lummus
timeline -- replies (when they arrive, via Lummus's own real inbound
pipeline) still show up correctly as reply_received events with zero
extra work here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS followup_campaigns (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    customer_email TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    cadence_days INTEGER NOT NULL,
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    next_send_at TEXT NOT NULL,
    last_sent_at TEXT,
    send_count INTEGER NOT NULL DEFAULT 0,
    last_mirrored_at TEXT
);
CREATE TABLE IF NOT EXISTS followup_send_log (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    to_email TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class FollowUpError(Exception):
    """Raised for a caller mistake (e.g. a case that already has an active
    campaign) -- safe to surface to the user."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class FollowUpStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        s = get_settings()
        self._db_path = db_path or s.STATE_DB_PATH
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def create(
        self,
        case_id: str,
        customer_email: str,
        requested_by: str,
        cadence_days: int,
        end_date: Optional[str] = None,
    ) -> str:
        await self._ensure_schema()
        if await self.get_active_for_case(case_id):
            raise FollowUpError("This invoice already has an active follow-up campaign. Cancel it first.")

        campaign_id = str(uuid.uuid4())
        now = _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO followup_campaigns "
                "(id, case_id, customer_email, requested_by, cadence_days, end_date, status, created_at, next_send_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (campaign_id, case_id, customer_email, requested_by, cadence_days, end_date, now, now),
            )
            await db.commit()
        return campaign_id

    async def get_active_for_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM followup_campaigns WHERE case_id = ? AND status = 'active'", (case_id,)
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_due(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        now_iso = (now or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM followup_campaigns WHERE status = 'active' AND next_send_at <= ?", (now_iso,)
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def mark_sent(self, campaign_id: str, to_email: str, cadence_days: int, end_date: Optional[str]) -> None:
        """Records the send, advances next_send_at by cadence_days, and
        marks the campaign completed if that would land past end_date."""
        await self._ensure_schema()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        next_send = now + timedelta(days=cadence_days)
        completed = bool(end_date) and next_send.date().isoformat() > end_date

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO followup_send_log (id, campaign_id, to_email) VALUES (?, ?, ?)",
                (str(uuid.uuid4()), campaign_id, to_email),
            )
            await db.execute(
                "UPDATE followup_campaigns SET last_sent_at = ?, send_count = send_count + 1, "
                "next_send_at = ?, status = ? WHERE id = ?",
                (now.isoformat(), next_send.isoformat(), "completed" if completed else "active", campaign_id),
            )
            await db.commit()

    async def cancel(self, case_id: str) -> bool:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE followup_campaigns SET status = 'cancelled' WHERE case_id = ? AND status = 'active'",
                (case_id,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def list_send_history(self, case_id: str) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT sl.sent_at, sl.to_email FROM followup_send_log sl "
                "JOIN followup_campaigns c ON c.id = sl.campaign_id "
                "WHERE c.case_id = ? ORDER BY sl.sent_at DESC",
                (case_id,),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def list_active(self) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM followup_campaigns WHERE status = 'active'")
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def mark_mirrored(self, campaign_id: str, when: str) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("UPDATE followup_campaigns SET last_mirrored_at = ? WHERE id = ?", (when, campaign_id))
            await db.commit()
