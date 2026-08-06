"""TraceRun / TraceStep persistence — every agent phase is visible."""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oa_trace_runs (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_oa_trace_runs_case ON oa_trace_runs(case_id, started_at);
CREATE TABLE IF NOT EXISTS oa_trace_steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    phase TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    ms INTEGER,
    reads_json TEXT NOT NULL DEFAULT '[]',
    writes_json TEXT NOT NULL DEFAULT '[]',
    call_json TEXT,
    principles_json TEXT NOT NULL DEFAULT '[]',
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_oa_trace_steps_run ON oa_trace_steps(run_id, seq);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class TraceStore:
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

    async def start_run(self, case_id: str, trigger: str) -> str:
        await self._ensure()
        run_id = f"tr_{uuid.uuid4().hex[:12]}"
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO oa_trace_runs (id, case_id, trigger, status, started_at) VALUES (?,?,?,?,?)",
                (run_id, case_id, trigger, "running", _now_iso()),
            )
            await db.commit()
        return run_id

    async def finish_run(self, run_id: str, status: str = "ok", summary: str = "") -> None:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE oa_trace_runs SET status=?, ended_at=?, summary=? WHERE id=?",
                (status, _now_iso(), summary, run_id),
            )
            await db.commit()

    async def add_step(
        self,
        run_id: str,
        seq: int,
        phase: str,
        title: str,
        *,
        status: str = "ok",
        reads: Optional[List[Dict[str, Any]]] = None,
        writes: Optional[List[Dict[str, Any]]] = None,
        call: Optional[Dict[str, Any]] = None,
        principles: Optional[List[str]] = None,
        error: Optional[str] = None,
        started_at: Optional[str] = None,
        ended_at: Optional[str] = None,
        ms: Optional[int] = None,
    ) -> str:
        await self._ensure()
        step_id = f"ts_{uuid.uuid4().hex[:12]}"
        started = started_at or _now_iso()
        ended = ended_at or _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO oa_trace_steps
                   (id, run_id, seq, phase, title, status, started_at, ended_at, ms,
                    reads_json, writes_json, call_json, principles_json, error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    step_id,
                    run_id,
                    seq,
                    phase,
                    title,
                    status,
                    started,
                    ended,
                    ms,
                    json.dumps(reads or []),
                    json.dumps(writes or []),
                    json.dumps(call) if call is not None else None,
                    json.dumps(principles or []),
                    error,
                ),
            )
            await db.commit()
        return step_id

    @asynccontextmanager
    async def step(
        self,
        run_id: str,
        seq: int,
        phase: str,
        title: str,
        *,
        principles: Optional[List[str]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Context manager that times a step and persists it."""
        started = _now_iso()
        t0 = time.perf_counter()
        bag: Dict[str, Any] = {
            "reads": [],
            "writes": [],
            "call": None,
            "status": "ok",
            "error": None,
            "principles": list(principles or []),
        }
        try:
            yield bag
        except Exception as exc:
            bag["status"] = "failed"
            bag["error"] = str(exc)
            raise
        finally:
            ms = int((time.perf_counter() - t0) * 1000)
            await self.add_step(
                run_id,
                seq,
                phase,
                title,
                status=bag.get("status") or "ok",
                reads=bag.get("reads"),
                writes=bag.get("writes"),
                call=bag.get("call"),
                principles=bag.get("principles"),
                error=bag.get("error"),
                started_at=started,
                ended_at=_now_iso(),
                ms=ms,
            )

    async def list_runs_for_case(self, case_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM oa_trace_runs WHERE case_id=? ORDER BY started_at DESC LIMIT ?",
                (case_id, limit),
            )
            return [dict(r) for r in await cur.fetchall()]

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM oa_trace_runs WHERE id=?", (run_id,))
            row = await cur.fetchone()
            if not row:
                return None
            run = dict(row)
            cur = await db.execute(
                "SELECT * FROM oa_trace_steps WHERE run_id=? ORDER BY seq ASC", (run_id,)
            )
            steps = []
            for r in await cur.fetchall():
                s = dict(r)
                s["reads"] = json.loads(s.pop("reads_json") or "[]")
                s["writes"] = json.loads(s.pop("writes_json") or "[]")
                s["call"] = json.loads(s.pop("call_json")) if s.get("call_json") else None
                s["principles"] = json.loads(s.pop("principles_json") or "[]")
                steps.append(s)
            run["steps"] = steps
            return run

    async def wipe_all(self) -> None:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM oa_trace_steps")
            await db.execute("DELETE FROM oa_trace_runs")
            await db.commit()
