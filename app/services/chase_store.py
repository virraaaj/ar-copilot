"""
SQLite-backed store for the agentic invoice chase engine (PLAN_AGENTIC_
CHASE.md, added 2026-07-20). One `chases` row per invoice pursuit: which
state it's in, who's currently being chased, what payment date (if any)
was promised, and how much of its nudge/miss budget is left. `chase_events`
is the full append-only history of everything that happened to a chase --
outreach sent, replies parsed, state changes, escalations -- both for
debugging and for the web Chases UI's event timeline. `chase_processed_mail`
dedupes inbound Graph mailbox reads (Phase C3), which are at-least-once.

Follows the same aiosqlite-against-STATE_DB_PATH pattern as
followup_store.py/digest_store.py/project_conversation_store.py -- see
those for the established idioms this copies.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from app.config import get_settings

# States a chase can be in -- see PLAN_AGENTIC_CHASE.md §4.1 for the full
# transition diagram. Two states are terminal: closed_paid, closed_manual.
OPEN_STATES = (
    "pending",
    "awaiting_pm",
    "awaiting_customer",
    "commitment_tracked",
    "verifying_payment",
    "escalated",
    "paused",
)
TERMINAL_STATES = ("closed_paid", "closed_manual")
ALL_STATES = OPEN_STATES + TERMINAL_STATES

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chases (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    case_key TEXT,
    invoice_no TEXT,
    project_number TEXT,
    subject_token TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    target TEXT,
    pm_email TEXT,
    customer_email TEXT,
    promised_date TEXT,
    promised_by TEXT,
    missed_count INTEGER NOT NULL DEFAULT 0,
    nudge_count INTEGER NOT NULL DEFAULT 0,
    clarify_count INTEGER NOT NULL DEFAULT 0,
    last_outreach_at TEXT,
    next_action_at TEXT,
    total_tokens_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chases_open_case
    ON chases(case_id)
    WHERE state NOT IN ('closed_paid', 'closed_manual');
CREATE TABLE IF NOT EXISTS chase_events (
    id TEXT PRIMARY KEY,
    chase_id TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS chase_processed_mail (
    message_id TEXT PRIMARY KEY,
    chase_id TEXT,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class ChaseError(Exception):
    """Raised for a caller mistake (e.g. creating a second open chase for
    a case that already has one) -- safe to surface to the user."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _new_subject_token() -> str:
    # Short, human-typeable, and distinct from any real invoice/case
    # identifier so subject-line matching (Phase C3) can't collide with
    # a customer quoting the invoice number in their reply.
    return f"AR-{uuid.uuid4().hex[:6].upper()}"


class ChaseStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        s = get_settings()
        self._db_path = db_path or s.STATE_DB_PATH
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            # Migration for chases tables created before total_tokens_used
            # existed (added 2026-07-22) -- CREATE TABLE IF NOT EXISTS
            # doesn't add columns to an already-existing table. SQLite has
            # no "ADD COLUMN IF NOT EXISTS", so just swallow the
            # "duplicate column" error on a table that's already migrated.
            try:
                await db.execute("ALTER TABLE chases ADD COLUMN total_tokens_used INTEGER NOT NULL DEFAULT 0")
                await db.commit()
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            await db.commit()
        self._initialized = True

    async def create(
        self,
        case_id: str,
        case_key: Optional[str] = None,
        invoice_no: Optional[str] = None,
        project_number: Optional[str] = None,
        next_action_at: Optional[str] = None,
    ) -> str:
        await self._ensure_schema()
        if await self.get_open_for_case(case_id):
            raise ChaseError(f"Case {case_id} already has an open chase.")

        chase_id = str(uuid.uuid4())
        now = _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO chases "
                "(id, case_id, case_key, invoice_no, project_number, subject_token, state, "
                " next_action_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                (chase_id, case_id, case_key, invoice_no, project_number, _new_subject_token(),
                 next_action_at or now, now, now),
            )
            await db.commit()
        await self.add_event(chase_id, "created", {"case_id": case_id, "case_key": case_key})
        return chase_id

    async def get(self, chase_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM chases WHERE id = ?", (chase_id,))
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_open_for_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        placeholders = ",".join("?" for _ in OPEN_STATES)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM chases WHERE case_id = ? AND state IN ({placeholders})",
                (case_id, *OPEN_STATES),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_by_subject_token(self, subject_token: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM chases WHERE subject_token = ?", (subject_token,))
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_due(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Open chases whose next_action_at has arrived -- the poller's
        main work queue. Excludes 'paused' and 'escalated', which are only
        ever moved by a human action, not by the tick."""
        await self._ensure_schema()
        now_iso = (now or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat()
        actionable = tuple(s for s in OPEN_STATES if s not in ("paused", "escalated"))
        placeholders = ",".join("?" for _ in actionable)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM chases WHERE state IN ({placeholders}) AND next_action_at <= ? "
                "ORDER BY next_action_at ASC",
                (*actionable, now_iso),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_latest_for_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Most recent chase for a case, open or terminal -- backs the
        get_chase_status chat tool, which should answer "what happened"
        even for a chase that's already closed, not just an open one."""
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM chases WHERE case_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (case_id,)
            )
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_open_for_project(self, project_number: str, target: Optional[str] = None) -> List[Dict[str, Any]]:
        """Open chases for a project, optionally filtered by current target
        -- backs the Teams inbound edge's "is this reply about one of my
        open chases" check (channels/teams/bot.py)."""
        await self._ensure_schema()
        placeholders = ",".join("?" for _ in OPEN_STATES)
        params: List[Any] = [project_number, *OPEN_STATES]
        target_clause = ""
        if target:
            target_clause = " AND target = ?"
            params.append(target)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM chases WHERE project_number = ? AND state IN ({placeholders}){target_clause}",
                params,
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def list_all(self, state: Optional[str] = None) -> List[Dict[str, Any]]:
        """Backs the web Chases tab. state=None returns every chase."""
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if state:
                cursor = await db.execute("SELECT * FROM chases WHERE state = ? ORDER BY updated_at DESC", (state,))
            else:
                cursor = await db.execute("SELECT * FROM chases ORDER BY updated_at DESC")
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update(self, chase_id: str, **fields: Any) -> None:
        """Generic field updater -- always bumps updated_at. Callers pass
        only the columns that changed (state, target, promised_date, …)."""
        if not fields:
            return
        await self._ensure_schema()
        fields = {**fields, "updated_at": _now_iso()}
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(f"UPDATE chases SET {set_clause} WHERE id = ?", (*fields.values(), chase_id))
            await db.commit()

    async def increment_tokens(self, chase_id: str, tokens: int) -> None:
        """Adds to the chase's running token total -- called after every
        LLM call made in service of this chase (reply parsing, message
        composition, trajectory assessment), added 2026-07-22 for the
        Chases UI's per-invoice token display. A no-op for tokens<=0 so a
        fallback path (0 tokens charged) never issues a pointless write."""
        if tokens <= 0:
            return
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE chases SET total_tokens_used = total_tokens_used + ?, updated_at = ? WHERE id = ?",
                (tokens, _now_iso(), chase_id),
            )
            await db.commit()

    async def add_event(self, chase_id: str, kind: str, detail: Optional[Dict[str, Any]] = None) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO chase_events (id, chase_id, kind, detail) VALUES (?, ?, ?, ?)",
                (str(uuid.uuid4()), chase_id, kind, json.dumps(detail) if detail is not None else None),
            )
            await db.commit()

    async def list_events(self, chase_id: str) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM chase_events WHERE chase_id = ? ORDER BY at ASC", (chase_id,)
            )
            rows = await cursor.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d["detail"]) if d["detail"] else None
            out.append(d)
        return out

    # ---- inbound mail dedupe (Phase C3) ------------------------------

    async def is_mail_processed(self, message_id: str) -> bool:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT 1 FROM chase_processed_mail WHERE message_id = ?", (message_id,)
            )
            return (await cursor.fetchone()) is not None

    async def mark_mail_processed(self, message_id: str, chase_id: Optional[str] = None) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO chase_processed_mail (message_id, chase_id) VALUES (?, ?)",
                (message_id, chase_id),
            )
            await db.commit()
