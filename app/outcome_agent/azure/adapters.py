"""Adapters that expose Azure stores with the same APIs the agent loop expects."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.outcome_agent.azure.cosmos_gremlin import CosmosGremlinGraph
from app.outcome_agent.azure.outbox import flush_graph_outbox
from app.outcome_agent.azure.pg_store import PostgresMemoryStore
from app.outcome_agent.azure.secrets import AzureMemorySettings

log = logging.getLogger(__name__)

_pg: Optional[PostgresMemoryStore] = None
_gremlin: Optional[CosmosGremlinGraph] = None
_settings: Optional[AzureMemorySettings] = None
_schema_ready = False


def _load_settings(settings=None) -> AzureMemorySettings:
    global _settings
    if _settings is not None:
        return _settings
    # Prefer env vars already on app settings
    import os

    from app.config import get_settings

    app = settings or get_settings()
    if getattr(app, "DATABASE_URL", None):
        os.environ.setdefault("DATABASE_URL", app.DATABASE_URL)
    if getattr(app, "COSMOS_GREMLIN_HOST", None):
        os.environ.setdefault("COSMOS_GREMLIN_HOST", app.COSMOS_GREMLIN_HOST)
    if getattr(app, "COSMOS_GREMLIN_USERNAME", None):
        os.environ.setdefault("COSMOS_GREMLIN_USERNAME", app.COSMOS_GREMLIN_USERNAME)
    if getattr(app, "COSMOS_GREMLIN_PASSWORD", None):
        os.environ.setdefault("COSMOS_GREMLIN_PASSWORD", app.COSMOS_GREMLIN_PASSWORD)
    vault = getattr(app, "AZURE_KEY_VAULT_NAME", None) or "chxaragentdev-kv"
    _settings = AzureMemorySettings.from_env_or_vault(vault)
    return _settings


def get_shared_pg(settings=None) -> PostgresMemoryStore:
    global _pg, _schema_ready
    if _pg is None:
        cfg = _load_settings(settings)
        _pg = PostgresMemoryStore(cfg.database_url)
    return _pg


def get_shared_gremlin(settings=None) -> CosmosGremlinGraph:
    global _gremlin
    if _gremlin is None:
        cfg = _load_settings(settings)
        _gremlin = CosmosGremlinGraph(cfg.gremlin_host, cfg.gremlin_username, cfg.gremlin_password)
    return _gremlin


async def ensure_pg_schema(pg: PostgresMemoryStore) -> None:
    global _schema_ready
    if _schema_ready:
        return
    await pg.apply_schema()
    _schema_ready = True


async def mirror_case_to_postgres(pg: PostgresMemoryStore, case: Dict[str, Any]) -> None:
    """Ensure the SQLite case row exists in Postgres (FK for facts/events)."""
    await ensure_pg_schema(pg)
    existing = await pg.get_case(case["id"])
    if existing:
        await pg.update_case(
            case["id"],
            state=case.get("state"),
            world=case.get("world"),
            dialogue=case.get("dialogue"),
            budget=case.get("budget"),
            goals=case.get("goals"),
            commitments=case.get("commitments"),
            blockers=case.get("blockers"),
            failed_asks=case.get("failed_asks"),
            last_decision=case.get("last_decision"),
            last_outreach_at=case.get("last_outreach_at"),
            next_action_at=case.get("next_action_at"),
        )
        return
    # Insert with the same id as SQLite
    await pg.connect()
    import json
    import uuid

    from app.outcome_agent.azure.pg_store import _jid

    async with pg._pool.acquire() as conn:  # noqa: SLF001
        await conn.execute(
            """
            INSERT INTO oa_cases (
                id, case_id, invoice_no, customer_name, customer_email, subject_token,
                state, amount, world_json, dialogue_json, budget_json, goals_json,
                commitments_json, blockers_json, failed_asks_json, next_action_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb,$12::jsonb,$13::jsonb,$14::jsonb,$15::jsonb,$16)
            ON CONFLICT (id) DO NOTHING
            """,
            case["id"],
            case.get("case_id") or f"case:{case.get('invoice_no') or uuid.uuid4().hex[:8]}",
            case.get("invoice_no"),
            case.get("customer_name"),
            case.get("customer_email"),
            case.get("subject_token") or f"AR-{uuid.uuid4().hex[:6].upper()}",
            case.get("state") or "overdue",
            case.get("amount"),
            _jid(case.get("world") or {}),
            _jid(case.get("dialogue") or {}),
            _jid(case.get("budget") or {}),
            _jid(case.get("goals") or {}),
            _jid(case.get("commitments") or []),
            _jid(case.get("blockers") or []),
            _jid(case.get("failed_asks") or []),
            _parse_ts(case.get("next_action_at")),
        )


def _parse_ts(val: Any) -> Any:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str):
        dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


MirrorFn = Callable[[PostgresMemoryStore, Dict[str, Any]], Awaitable[None]]


class AzureSemanticStore:
    def __init__(self, pg: PostgresMemoryStore, case_store: Any, mirror: MirrorFn) -> None:
        self.pg = pg
        self.case_store = case_store
        self.mirror = mirror

    async def _ensure_case(self, case_id: str) -> None:
        case = await self.case_store.get(case_id)
        if case:
            await self.mirror(self.pg, case)

    async def list_active(self, case_id: str) -> List[Dict[str, Any]]:
        await self._ensure_case(case_id)
        facts = await self.pg.list_facts(case_id)
        # Shape expected by loop / UI
        return [
            {
                "id": f["id"],
                "case_id": case_id,
                "kind": f["kind"],
                "key": f["key"],
                "value": f["value"],
                "confidence": f["confidence"],
                "status": "active",
                "backend": "postgres",
            }
            for f in facts
        ]

    async def upsert(
        self,
        case_id: str,
        kind: str,
        key: str,
        value: Any,
        *,
        confidence: float = 1.0,
        source_event_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure_case(case_id)
        fact = await self.pg.upsert_fact(case_id, kind, key, value, confidence=confidence)
        fact["backend"] = "postgres"
        return fact


class AzureEventLedger:
    def __init__(self, pg: PostgresMemoryStore, case_store: Any, mirror: MirrorFn) -> None:
        self.pg = pg
        self.case_store = case_store
        self.mirror = mirror

    async def append(
        self,
        case_row_id: str,
        kind: str,
        detail: Dict[str, Any],
        *,
        at: Optional[str] = None,
        principles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        case = await self.case_store.get(case_row_id)
        if case:
            await self.mirror(self.pg, case)
        ev = await self.pg.append_event(case_row_id, kind, detail, principles=principles)
        ev["backend"] = "postgres"
        return ev

    async def list_for_case(self, case_row_id: str) -> List[Dict[str, Any]]:
        case = await self.case_store.get(case_row_id)
        if case:
            await self.mirror(self.pg, case)
        events = await self.pg.list_events(case_row_id)
        for e in events:
            e["backend"] = "postgres"
        return events


class AzureLearningStore:
    def __init__(self, pg: PostgresMemoryStore) -> None:
        self.pg = pg

    async def tactic_weights(self) -> Dict[str, float]:
        await ensure_pg_schema(self.pg)
        return await self.pg.tactic_weights()

    async def record(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        await ensure_pg_schema(self.pg)
        return await self.pg.record_learning(*args, **kwargs)

    async def summary(self) -> Dict[str, Any]:
        weights = await self.tactic_weights()
        return {"tactic_weights": weights, "backend": "postgres"}


class CosmosGraphAdapter:
    """GraphStore-compatible async facade over Cosmos Gremlin + Postgres outbox."""

    def __init__(self, pg: PostgresMemoryStore, gremlin: CosmosGremlinGraph) -> None:
        self.pg = pg
        self.gremlin = gremlin

    def _pk(self, node_id: str) -> str:
        # invoice:INV-1 → INV-1; use last segment
        return node_id.split(":")[-1] if ":" in node_id else node_id

    def _now(self) -> str:
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    async def upsert_node(
        self,
        node_id: str,
        node_type: str,
        label: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        await ensure_pg_schema(self.pg)
        pk = self._pk(node_id)
        props = {"node_label": label or node_id, **(attributes or {})}
        await self.pg.enqueue_graph(
            "upsert_vertex",
            {"id": node_id, "label": node_type, "pk": pk, "props": {k: str(v) for k, v in props.items()}},
        )
        await flush_graph_outbox(self.pg, self.gremlin)

    async def supersede_edge(
        self,
        from_node_id: str,
        relationship: str,
        to_node_id: str,
        observed_at: Optional[str] = None,
        source_event_id: Optional[str] = None,
        confidence: float = 1.0,
    ) -> str:
        await ensure_pg_schema(self.pg)
        pk = self._pk(from_node_id)
        to_label = "Commitment" if "commitment" in to_node_id.lower() else (
            "Blocker" if "blocker" in to_node_id.lower() else "Entity"
        )
        await self.pg.enqueue_graph(
            "supersede_edge",
            {
                "from_id": from_node_id,
                "rel": relationship,
                "to_id": to_node_id,
                "valid_from": observed_at or self._now(),
                "pk": pk,
                "to_label": to_label,
                "to_props": {"node_label": to_node_id},
            },
        )
        await flush_graph_outbox(self.pg, self.gremlin)
        return f"{from_node_id}|{relationship}|{to_node_id}"

    async def neighborhood(self, node_id: str, active_only: bool = True) -> Dict[str, Any]:
        pk = self._pk(node_id)
        nb = await asyncio.to_thread(self.gremlin.neighborhood, node_id, pk)
        # Normalize to GraphStore-ish shape for UI
        edges = []
        for e in nb.get("out_edges") or []:
            if isinstance(e, dict):
                edges.append(
                    {
                        "from_node_id": node_id,
                        "relationship": e.get("label"),
                        "to_node_id": e.get("to"),
                        "valid_from": e.get("valid_from"),
                        "valid_to": e.get("valid_to") or None,
                        "backend": "cosmos_gremlin",
                    }
                )
        return {"nodes": {node_id: {"id": node_id}}, "edges": edges, "backend": "cosmos_gremlin", "raw": nb}

    async def list_edges(
        self, node_id: str, relationship: Optional[str] = None, active_only: bool = True, direction: str = "from"
    ) -> List[Dict[str, Any]]:
        nb = await self.neighborhood(node_id, active_only=active_only)
        edges = nb.get("edges") or []
        if relationship:
            edges = [e for e in edges if e.get("relationship") == relationship]
        return edges
