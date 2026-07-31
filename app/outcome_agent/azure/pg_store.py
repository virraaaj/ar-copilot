"""Postgres must-have stores: case, events, facts, learning, mailbox, outbox."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg

_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "scripts" / "azure" / "schema.sql"

_OUTCOME_DELTAS = {
    "kept": 0.3,
    "got_commitment": 0.2,
    "missed": -0.4,
    "ignored": -0.25,
    "dispute": -0.15,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jid(obj: Any) -> str:
    return json.dumps(obj if obj is not None else {})


def _as_json(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


class PostgresMemoryStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.database_url, min_size=1, max_size=5)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def apply_schema(self) -> None:
        await self.connect()
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        async with self._pool.acquire() as conn:
            await conn.execute(sql)

    async def wipe_demo_case(self, invoice_no: str) -> None:
        await self.connect()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT id FROM oa_cases WHERE invoice_no = $1", invoice_no)
            for r in rows:
                await conn.execute("DELETE FROM oa_cases WHERE id = $1", r["id"])

    async def create_case(
        self,
        *,
        invoice_no: str,
        customer_name: str,
        amount: float,
        customer_email: str = "ap@example.com",
        state: str = "overdue",
    ) -> Dict[str, Any]:
        await self.connect()
        row_id = str(uuid.uuid4())
        case_id = f"case:{invoice_no}"
        token = f"AR-{uuid.uuid4().hex[:6].upper()}"
        world = {"invoice_no": invoice_no, "balance_due": amount, "currency": "USD"}
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO oa_cases (
                    id, case_id, invoice_no, customer_name, customer_email, subject_token,
                    state, amount, world_json, dialogue_json, budget_json, goals_json,
                    commitments_json, blockers_json, next_action_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb, NOW())
                """,
                row_id,
                case_id,
                invoice_no,
                customer_name,
                customer_email,
                token,
                state,
                amount,
                _jid(world),
                _jid({}),
                _jid({"max_unanswered": 5, "unanswered_count": 0}),
                _jid({"current_objective": "obtain_payment_date"}),
                _jid([]),
                _jid([]),
            )
        return await self.get_case(row_id)

    async def get_case(self, case_row_id: str) -> Optional[Dict[str, Any]]:
        await self.connect()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM oa_cases WHERE id = $1", case_row_id)
        return self._case_from_row(row) if row else None

    async def update_case(self, case_row_id: str, **fields: Any) -> Dict[str, Any]:
        mapping = {
            "state": "state",
            "world": "world_json",
            "dialogue": "dialogue_json",
            "budget": "budget_json",
            "goals": "goals_json",
            "commitments": "commitments_json",
            "blockers": "blockers_json",
            "failed_asks": "failed_asks_json",
            "last_decision": "last_decision_json",
            "last_outreach_at": "last_outreach_at",
            "next_action_at": "next_action_at",
        }
        sets = ["updated_at = NOW()"]
        args: List[Any] = []
        for k, col in mapping.items():
            if k not in fields:
                continue
            val = fields[k]
            if col.endswith("_json"):
                val = _jid(val)
            elif col.endswith("_at") and isinstance(val, str):
                # asyncpg wants datetime for timestamptz
                val = datetime.fromisoformat(val.replace("Z", "+00:00"))
                if val.tzinfo is None:
                    val = val.replace(tzinfo=timezone.utc)
            args.append(val)
            sets.append(f"{col} = ${len(args)}" + ("::jsonb" if col.endswith("_json") else ""))
        args.append(case_row_id)
        sql = f"UPDATE oa_cases SET {', '.join(sets)} WHERE id = ${len(args)}"
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)
        return await self.get_case(case_row_id)

    async def append_event(
        self, case_row_id: str, kind: str, detail: Dict[str, Any], principles: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        eid = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO oa_events (id, case_id, kind, detail_json, principles_json)
                VALUES ($1,$2,$3,$4::jsonb,$5::jsonb)
                """,
                eid,
                case_row_id,
                kind,
                _jid(detail),
                _jid(principles or []),
            )
            row = await conn.fetchrow("SELECT * FROM oa_events WHERE id = $1", eid)
        return {
            "id": row["id"],
            "case_id": row["case_id"],
            "at": row["at"].isoformat(),
            "kind": row["kind"],
            "detail": _as_json(row["detail_json"], {}),
            "principles": _as_json(row["principles_json"], []),
        }

    async def list_events(self, case_row_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM oa_events WHERE case_id = $1 ORDER BY at ASC", case_row_id
            )
        return [
            {
                "id": r["id"],
                "at": r["at"].isoformat(),
                "kind": r["kind"],
                "detail": _as_json(r["detail_json"], {}),
                "principles": _as_json(r["principles_json"], []),
            }
            for r in rows
        ]

    async def upsert_fact(
        self, case_row_id: str, kind: str, key: str, value: Any, confidence: float = 1.0
    ) -> Dict[str, Any]:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE oa_memory_facts
                SET status = 'superseded', superseded_at = NOW()
                WHERE case_id = $1 AND kind = $2 AND key = $3 AND status = 'active'
                """,
                case_row_id,
                kind,
                key,
            )
            fid = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO oa_memory_facts (id, case_id, kind, key, value_json, confidence, status)
                VALUES ($1,$2,$3,$4,$5::jsonb,$6,'active')
                """,
                fid,
                case_row_id,
                kind,
                key,
                _jid(value),
                confidence,
            )
            row = await conn.fetchrow("SELECT * FROM oa_memory_facts WHERE id = $1", fid)
        return {
            "id": row["id"],
            "kind": row["kind"],
            "key": row["key"],
            "value": _as_json(row["value_json"]),
            "confidence": row["confidence"],
            "status": row["status"],
        }

    async def list_facts(self, case_row_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM oa_memory_facts WHERE case_id = $1 AND status = 'active' ORDER BY created_at",
                case_row_id,
            )
        return [
            {
                "id": r["id"],
                "kind": r["kind"],
                "key": r["key"],
                "value": _as_json(r["value_json"]),
                "confidence": r["confidence"],
            }
            for r in rows
        ]

    async def record_learning(
        self, ask_id: str, case_row_id: str, tactic: str, outcome: str, objective: str = ""
    ) -> Dict[str, Any]:
        delta = _OUTCOME_DELTAS.get(outcome, 0.0)
        rid = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO oa_learning
                    (id, ask_id, case_id, tactic, objective, outcome, weight_delta)
                    VALUES ($1,$2,$3,$4,$5,$6,$7)
                    """,
                    rid,
                    ask_id,
                    case_row_id,
                    tactic,
                    objective,
                    outcome,
                    delta,
                )
                await conn.execute(
                    """
                    INSERT INTO oa_tactic_weights (tactic, objective, weight)
                    VALUES ($1,$2,$3)
                    ON CONFLICT (tactic, objective)
                    DO UPDATE SET weight = oa_tactic_weights.weight + EXCLUDED.weight
                    """,
                    tactic,
                    objective or "obtain_payment_date",
                    delta,
                )
        return {"id": rid, "tactic": tactic, "outcome": outcome, "weight_delta": delta}

    async def tactic_weights(self) -> Dict[str, float]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT tactic, SUM(weight) AS w FROM oa_tactic_weights GROUP BY tactic")
        return {r["tactic"]: float(r["w"]) for r in rows}

    async def add_mailbox(
        self,
        case_row_id: str,
        direction: str,
        subject: str,
        body: str,
        provider_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        mid = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO oa_mailbox_messages
                (id, case_id, direction, provider_message_id, subject, body_preview)
                VALUES ($1,$2,$3,$4,$5,$6)
                """,
                mid,
                case_row_id,
                direction,
                provider_message_id or mid,
                subject,
                body[:500],
            )
        return {"id": mid, "direction": direction, "subject": subject, "body_preview": body[:500]}

    async def list_mailbox(self, case_row_id: str) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM oa_mailbox_messages WHERE case_id = $1 ORDER BY at ASC", case_row_id
            )
        return [
            {
                "id": r["id"],
                "direction": r["direction"],
                "subject": r["subject"],
                "body_preview": r["body_preview"],
                "at": r["at"].isoformat(),
            }
            for r in rows
        ]

    async def enqueue_graph(self, op: str, payload: Dict[str, Any]) -> str:
        oid = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO oa_graph_outbox (id, op, payload_json, status)
                VALUES ($1,$2,$3::jsonb,'pending')
                """,
                oid,
                op,
                _jid(payload),
            )
        return oid

    async def list_pending_outbox(self, limit: int = 50) -> List[Dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM oa_graph_outbox
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT $1
                """,
                limit,
            )
        return [
            {
                "id": r["id"],
                "op": r["op"],
                "payload": _as_json(r["payload_json"], {}),
                "attempts": r["attempts"],
            }
            for r in rows
        ]

    async def mark_outbox(self, outbox_id: str, *, ok: bool, error: Optional[str] = None) -> None:
        async with self._pool.acquire() as conn:
            if ok:
                await conn.execute(
                    """
                    UPDATE oa_graph_outbox
                    SET status = 'done', processed_at = NOW(), last_error = NULL
                    WHERE id = $1
                    """,
                    outbox_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE oa_graph_outbox
                    SET attempts = attempts + 1, last_error = $2,
                        status = CASE WHEN attempts + 1 >= 5 THEN 'failed' ELSE 'pending' END
                    WHERE id = $1
                    """,
                    outbox_id,
                    error,
                )

    async def snapshot(self, case_row_id: str) -> Dict[str, Any]:
        case = await self.get_case(case_row_id)
        return {
            "case": {
                "id": case["id"],
                "invoice_no": case["invoice_no"],
                "state": case["state"],
                "commitments": case["commitments"],
                "blockers": case["blockers"],
                "dialogue": case["dialogue"],
            },
            "events": await self.list_events(case_row_id),
            "facts": await self.list_facts(case_row_id),
            "mailbox": await self.list_mailbox(case_row_id),
            "tactic_weights": await self.tactic_weights(),
            "pending_outbox": len(await self.list_pending_outbox()),
        }

    @staticmethod
    def _case_from_row(row: asyncpg.Record) -> Dict[str, Any]:
        d = dict(row)
        for col, name, default in (
            ("world_json", "world", {}),
            ("dialogue_json", "dialogue", {}),
            ("budget_json", "budget", {}),
            ("goals_json", "goals", {}),
            ("commitments_json", "commitments", []),
            ("blockers_json", "blockers", []),
            ("failed_asks_json", "failed_asks", []),
            ("escalation_json", "escalation", None),
            ("last_decision_json", "last_decision", None),
        ):
            d[name] = _as_json(d.pop(col, None), default)
        for tcol in ("created_at", "updated_at", "last_outreach_at", "next_action_at"):
            if d.get(tcol) is not None:
                d[tcol] = d[tcol].isoformat()
        return d
