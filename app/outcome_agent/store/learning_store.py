"""Credit-assignment / tactic weight store (P5, P10, P13)."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oa_learning (
    id TEXT PRIMARY KEY,
    ask_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    tactic TEXT NOT NULL,
    objective TEXT,
    outcome TEXT NOT NULL,
    produced_commitment_id TEXT,
    weight_delta REAL NOT NULL DEFAULT 0,
    scored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS oa_tactic_weights (
    tactic TEXT NOT NULL,
    objective TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (tactic, objective)
);
"""

_OUTCOME_DELTAS = {
    "kept": 0.3,
    "got_commitment": 0.2,
    "missed": -0.4,
    "ignored": -0.25,
    "dispute": -0.15,
    "negative_sentiment": -0.35,
    "escalated": -0.1,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class LearningStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
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
            await db.execute("DELETE FROM oa_learning")
            await db.execute("DELETE FROM oa_tactic_weights")
            await db.commit()

    async def record(
        self,
        ask_id: str,
        case_id: str,
        tactic: str,
        outcome: str,
        *,
        objective: str = "",
        produced_commitment_id: Optional[str] = None,
        scored_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure_schema()
        delta = _OUTCOME_DELTAS.get(outcome, 0.0)
        rid = str(uuid.uuid4())
        at = scored_at or _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO oa_learning
                (id, ask_id, case_id, tactic, objective, outcome, produced_commitment_id, weight_delta, scored_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    rid,
                    ask_id,
                    case_id,
                    tactic,
                    objective,
                    outcome,
                    produced_commitment_id,
                    delta,
                    at,
                ),
            )
            await db.execute(
                """
                INSERT INTO oa_tactic_weights (tactic, objective, weight)
                VALUES (?,?,?)
                ON CONFLICT(tactic, objective) DO UPDATE SET weight = weight + excluded.weight
                """,
                (tactic, objective or "*", delta),
            )
            await db.commit()
        return {
            "id": rid,
            "ask_id": ask_id,
            "case_id": case_id,
            "tactic": tactic,
            "objective": objective,
            "outcome": outcome,
            "produced_commitment_id": produced_commitment_id,
            "weight_delta": delta,
            "scored_at": at,
        }

    async def tactic_weights(self, objective: Optional[str] = None) -> Dict[str, float]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            if objective:
                cur = await db.execute(
                    "SELECT tactic, weight FROM oa_tactic_weights WHERE objective IN (?, '*')",
                    (objective,),
                )
            else:
                cur = await db.execute("SELECT tactic, weight FROM oa_tactic_weights")
            rows = await cur.fetchall()
        weights: Dict[str, float] = {}
        for tactic, weight in rows:
            weights[tactic] = weights.get(tactic, 0.0) + float(weight)
        return weights

    async def list_for_case(self, case_id: str) -> List[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM oa_learning WHERE case_id = ? ORDER BY scored_at ASC",
                (case_id,),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def summary(self) -> Dict[str, Any]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "SELECT outcome, COUNT(*), COALESCE(SUM(weight_delta),0) FROM oa_learning GROUP BY outcome"
            )
            by_outcome = {
                row[0]: {"count": row[1], "weight_delta_sum": row[2]} for row in await cur.fetchall()
            }
            cur = await db.execute(
                "SELECT tactic, objective, weight FROM oa_tactic_weights ORDER BY weight ASC"
            )
            weights = [
                {"tactic": r[0], "objective": r[1], "weight": r[2]} for r in await cur.fetchall()
            ]
        kept = by_outcome.get("kept", {}).get("count", 0)
        missed = by_outcome.get("missed", {}).get("count", 0)
        return {
            "by_outcome": by_outcome,
            "kept_promises": kept,
            "missed_promises": missed,
            "tactic_weights": weights,
        }
