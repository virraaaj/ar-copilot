"""Append-only event ledger for outcome-agent cases."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oa_events (
    id TEXT PRIMARY KEY,
    case_row_id TEXT NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT,
    principles_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_oa_events_case ON oa_events(case_row_id, at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class EventLedger:
    def __init__(self, db_path: Optional[str] = None) -> None:
        # Avoid get_settings() when tests pass an explicit path.
        self._db_path = db_path if db_path is not None else get_settings().STATE_DB_PATH
        self._initialized = False

    @property
    def db_path(self) -> str:
        return self._db_path

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def wipe_all(self) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM oa_events")
            await db.commit()

    async def append(
        self,
        case_row_id: str,
        kind: str,
        detail: Optional[Dict[str, Any]] = None,
        *,
        at: Optional[str] = None,
        principles: Optional[List[str]] = None,
    ) -> str:
        await self._ensure_schema()
        eid = str(uuid.uuid4())
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO oa_events (id, case_row_id, at, kind, detail, principles_json) "
                "VALUES (?,?,?,?,?,?)",
                (
                    eid,
                    case_row_id,
                    at or _now_iso(),
                    kind,
                    json.dumps(detail or {}),
                    json.dumps(principles or []),
                ),
            )
            await db.commit()
        return eid

    async def list_for_case(self, case_row_id: str) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM oa_events WHERE case_row_id = ? ORDER BY at ASC",
                (case_row_id,),
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d.pop("detail") or "{}")
            except json.JSONDecodeError:
                d["detail"] = {}
            try:
                d["principles"] = json.loads(d.pop("principles_json") or "[]")
            except json.JSONDecodeError:
                d["principles"] = []
            out.append(d)
        return out

    async def list_by_kind(self, kinds: List[str], limit: int = 200) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        if not kinds:
            return []
        placeholders = ",".join("?" * len(kinds))
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM oa_events WHERE kind IN ({placeholders}) "
                "ORDER BY at DESC LIMIT ?",
                (*kinds, limit),
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["detail"] = json.loads(d.pop("detail") or "{}")
            except json.JSONDecodeError:
                d["detail"] = {}
            d.pop("principles_json", None)
            out.append(d)
        return out
