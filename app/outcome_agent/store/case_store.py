"""SQLite CaseStore — replaces ChaseStore as the outcome-agent runtime store."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite

from app.config import get_settings
from app.outcome_agent.domain.types import OPEN_STATES, TERMINAL_STATES

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oa_cases (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    case_key TEXT,
    invoice_no TEXT,
    project_number TEXT,
    customer_name TEXT,
    subject_token TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    target TEXT,
    pm_email TEXT,
    customer_email TEXT,
    contact_email TEXT,
    amount REAL,
    world_json TEXT NOT NULL DEFAULT '{}',
    dialogue_json TEXT NOT NULL DEFAULT '{}',
    budget_json TEXT NOT NULL DEFAULT '{}',
    goals_json TEXT NOT NULL DEFAULT '{}',
    commitments_json TEXT NOT NULL DEFAULT '[]',
    blockers_json TEXT NOT NULL DEFAULT '[]',
    failed_asks_json TEXT NOT NULL DEFAULT '[]',
    escalation_json TEXT,
    last_decision_json TEXT,
    last_outreach_at TEXT,
    next_action_at TEXT,
    paused INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_oa_cases_open_case
    ON oa_cases(case_id)
    WHERE state NOT IN ('paid', 'closed', 'disputed', 'suppressed');
CREATE TABLE IF NOT EXISTS oa_decision_traces (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oa_processed_mail (
    message_id TEXT PRIMARY KEY,
    case_row_id TEXT,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS oa_outbox (
    id TEXT PRIMARY KEY,
    case_row_id TEXT NOT NULL,
    at TEXT NOT NULL,
    channel TEXT NOT NULL,
    recipient TEXT,
    subject TEXT,
    body TEXT NOT NULL,
    dry_run INTEGER NOT NULL DEFAULT 1,
    meta_json TEXT
);
"""


class CaseError(Exception):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _new_subject_token() -> str:
    return f"AR-{uuid.uuid4().hex[:6].upper()}"


def _json_loads(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _row_to_case(row: aiosqlite.Row) -> Dict[str, Any]:
    d = dict(row)
    for key, default in (
        ("world_json", {}),
        ("dialogue_json", {}),
        ("budget_json", {}),
        ("goals_json", {}),
        ("commitments_json", []),
        ("blockers_json", []),
        ("failed_asks_json", []),
        ("escalation_json", None),
        ("last_decision_json", None),
    ):
        raw = d.pop(key, None)
        name = key.replace("_json", "")
        if name == "escalation":
            d["escalation"] = _json_loads(raw, None)
        elif name == "last_decision":
            d["last_decision"] = _json_loads(raw, None)
        elif name in ("commitments", "blockers", "failed_asks"):
            d[name] = _json_loads(raw, [])
        else:
            d[name] = _json_loads(raw, default if default is not None else {})
    d["paused"] = bool(d.get("paused"))
    return d


class CaseStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path if db_path is not None else get_settings().STATE_DB_PATH
        self._initialized = False
        parent = Path(self._db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)

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
            await db.execute("DELETE FROM oa_outbox")
            await db.execute("DELETE FROM oa_decision_traces")
            await db.execute("DELETE FROM oa_processed_mail")
            await db.execute("DELETE FROM oa_cases")
            await db.commit()

    async def create(
        self,
        case_id: str,
        *,
        case_key: Optional[str] = None,
        invoice_no: Optional[str] = None,
        project_number: Optional[str] = None,
        customer_name: Optional[str] = None,
        state: str = "overdue",
        next_action_at: Optional[str] = None,
        amount: Optional[float] = None,
        world: Optional[Dict[str, Any]] = None,
        dialogue: Optional[Dict[str, Any]] = None,
        budget: Optional[Dict[str, Any]] = None,
        goals: Optional[Dict[str, Any]] = None,
        commitments: Optional[List[Dict[str, Any]]] = None,
        blockers: Optional[List[Dict[str, Any]]] = None,
        pm_email: Optional[str] = None,
        customer_email: Optional[str] = None,
        target: Optional[str] = None,
        case_row_id: Optional[str] = None,
    ) -> str:
        await self._ensure_schema()
        existing = await self.get_open_for_case(case_id)
        if existing:
            raise CaseError(f"Open case already exists for {case_id}")
        row_id = case_row_id or str(uuid.uuid4())
        token = _new_subject_token()
        now = _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO oa_cases (
                    id, case_id, case_key, invoice_no, project_number, customer_name,
                    subject_token, state, target, pm_email, customer_email, amount,
                    world_json, dialogue_json, budget_json, goals_json,
                    commitments_json, blockers_json, next_action_at, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row_id,
                    case_id,
                    case_key,
                    invoice_no,
                    project_number,
                    customer_name,
                    token,
                    state,
                    target,
                    pm_email,
                    customer_email,
                    amount,
                    json.dumps(world or {}),
                    json.dumps(dialogue or {}),
                    json.dumps(budget or {}),
                    json.dumps(goals or {}),
                    json.dumps(commitments or []),
                    json.dumps(blockers or []),
                    next_action_at,
                    now,
                    now,
                ),
            )
            await db.commit()
        return row_id

    async def get(self, case_row_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM oa_cases WHERE id = ?", (case_row_id,))
            row = await cur.fetchone()
        return _row_to_case(row) if row else None

    async def get_by_invoice(self, invoice_no: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM oa_cases WHERE invoice_no = ? ORDER BY created_at DESC LIMIT 1",
                (invoice_no,),
            )
            row = await cur.fetchone()
        return _row_to_case(row) if row else None

    async def get_open_for_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        terminals = tuple(TERMINAL_STATES)
        placeholders = ",".join("?" * len(terminals))
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM oa_cases WHERE case_id = ? AND state NOT IN ({placeholders}) "
                "ORDER BY created_at DESC LIMIT 1",
                (case_id, *terminals),
            )
            row = await cur.fetchone()
        return _row_to_case(row) if row else None

    async def get_by_subject_token(self, subject_token: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM oa_cases WHERE subject_token = ?", (subject_token,)
            )
            row = await cur.fetchone()
        return _row_to_case(row) if row else None

    async def get_latest_for_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM oa_cases WHERE case_id = ? ORDER BY created_at DESC LIMIT 1",
                (case_id,),
            )
            row = await cur.fetchone()
        return _row_to_case(row) if row else None

    async def list_due(self, now: Optional[str] = None) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        now = now or _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT * FROM oa_cases
                WHERE next_action_at IS NOT NULL AND next_action_at <= ?
                  AND state NOT IN ('paid','closed','disputed','suppressed','paused','escalated_to_human')
                ORDER BY next_action_at ASC
                """,
                (now,),
            )
            rows = await cur.fetchall()
        return [_row_to_case(r) for r in rows]

    async def list_open_for_project(
        self, project_number: str, target: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        sql = """
            SELECT * FROM oa_cases
            WHERE project_number = ?
              AND state NOT IN ('paid','closed','disputed','suppressed')
        """
        params: List[Any] = [project_number]
        if target:
            sql += " AND target = ?"
            params.append(target)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(sql, params)
            rows = await cur.fetchall()
        return [_row_to_case(r) for r in rows]

    async def list_all(
        self, state: Optional[str] = None, case_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        clauses: List[str] = []
        params: List[Any] = []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if case_id:
            clauses.append("case_id = ?")
            params.append(case_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM oa_cases {where} ORDER BY updated_at DESC", params
            )
            rows = await cur.fetchall()
        return [_row_to_case(r) for r in rows]

    async def update(self, case_row_id: str, **fields: Any) -> None:
        await self._ensure_schema()
        if not fields:
            return
        json_map = {
            "world": "world_json",
            "dialogue": "dialogue_json",
            "budget": "budget_json",
            "goals": "goals_json",
            "commitments": "commitments_json",
            "blockers": "blockers_json",
            "failed_asks": "failed_asks_json",
            "escalation": "escalation_json",
            "last_decision": "last_decision_json",
        }
        cols: List[str] = []
        vals: List[Any] = []
        for k, v in fields.items():
            if k in json_map:
                cols.append(f"{json_map[k]} = ?")
                vals.append(json.dumps(v))
            elif k == "paused":
                cols.append("paused = ?")
                vals.append(1 if v else 0)
            else:
                cols.append(f"{k} = ?")
                vals.append(v)
        cols.append("updated_at = ?")
        vals.append(_now_iso())
        vals.append(case_row_id)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                f"UPDATE oa_cases SET {', '.join(cols)} WHERE id = ?", vals
            )
            await db.commit()

    async def add_decision_trace(self, case_row_id: str, trace: Dict[str, Any]) -> str:
        await self._ensure_schema()
        tid = trace.get("id") or str(uuid.uuid4())
        trace = {**trace, "id": tid}
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO oa_decision_traces (id, case_id, at, payload) VALUES (?,?,?,?)",
                (tid, case_row_id, trace.get("at") or _now_iso(), json.dumps(trace)),
            )
            await db.commit()
        await self.update(case_row_id, last_decision=trace)
        return tid

    async def list_decision_traces(self, case_row_id: str) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT payload FROM oa_decision_traces WHERE case_id = ? ORDER BY at ASC",
                (case_row_id,),
            )
            rows = await cur.fetchall()
        out = []
        for r in rows:
            out.append(_json_loads(r["payload"], {}))
        return out

    async def add_outbox(
        self,
        case_row_id: str,
        body: str,
        *,
        channel: str = "email",
        recipient: Optional[str] = None,
        subject: Optional[str] = None,
        dry_run: bool = False,
        meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        await self._ensure_schema()
        oid = str(uuid.uuid4())
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO oa_outbox (id, case_row_id, at, channel, recipient, subject, body, dry_run, meta_json)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    oid,
                    case_row_id,
                    _now_iso(),
                    channel,
                    recipient,
                    subject,
                    body,
                    1 if dry_run else 0,
                    json.dumps(meta or {}),
                ),
            )
            await db.commit()
        return oid

    async def list_outbox(self, limit: int = 200) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM oa_outbox ORDER BY at DESC LIMIT ?", (limit,)
            )
            rows = await cur.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["dry_run"] = bool(d.get("dry_run"))
            d["meta"] = _json_loads(d.pop("meta_json", None), {})
            result.append(d)
        return result

    async def is_mail_processed(self, message_id: str) -> bool:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT 1 FROM oa_processed_mail WHERE message_id = ?", (message_id,)
            )
            return (await cur.fetchone()) is not None

    async def mark_mail_processed(
        self, message_id: str, case_row_id: Optional[str] = None
    ) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO oa_processed_mail (message_id, case_row_id) VALUES (?,?)",
                (message_id, case_row_id),
            )
            await db.commit()
