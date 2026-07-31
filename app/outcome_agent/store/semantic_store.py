"""First-class semantic memory facts."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oa_memory_facts (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    customer_id TEXT,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active',
    source_event_id TEXT,
    created_at TEXT NOT NULL,
    superseded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_oa_facts_case ON oa_memory_facts(case_id, status);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class SemanticStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path if db_path is not None else get_settings().STATE_DB_PATH
        self._initialized = False

    @property
    def db_path(self) -> str:
        return self._db_path

    async def _ensure(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def list_active(self, case_id: str) -> List[Dict[str, Any]]:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM oa_memory_facts WHERE case_id=? AND status='active' ORDER BY created_at",
                (case_id,),
            )
            out = []
            for r in await cur.fetchall():
                d = dict(r)
                d["value"] = json.loads(d.pop("value_json") or "null")
                out.append(d)
            return out

    async def upsert(
        self,
        case_id: str,
        kind: str,
        key: str,
        value: Any,
        *,
        confidence: float = 1.0,
        customer_id: Optional[str] = None,
        source_event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure()
        now = _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """UPDATE oa_memory_facts SET status='superseded', superseded_at=?
                   WHERE case_id=? AND key=? AND status='active'""",
                (now, case_id, key),
            )
            fid = f"fact_{uuid.uuid4().hex[:10]}"
            await db.execute(
                """INSERT INTO oa_memory_facts
                   (id, case_id, customer_id, kind, key, value_json, confidence, status, source_event_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    fid,
                    case_id,
                    customer_id,
                    kind,
                    key,
                    json.dumps(value),
                    confidence,
                    "active",
                    source_event_id,
                    now,
                ),
            )
            await db.commit()
        return {
            "id": fid,
            "case_id": case_id,
            "kind": kind,
            "key": key,
            "value": value,
            "confidence": confidence,
            "status": "active",
            "created_at": now,
        }

    async def wipe_all(self) -> None:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM oa_memory_facts")
            await db.commit()
