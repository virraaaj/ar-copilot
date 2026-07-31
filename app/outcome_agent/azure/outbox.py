"""Flush Postgres graph outbox → Cosmos Gremlin."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from app.outcome_agent.azure.cosmos_gremlin import CosmosGremlinGraph
from app.outcome_agent.azure.pg_store import PostgresMemoryStore

log = logging.getLogger(__name__)


def _apply_op(graph: CosmosGremlinGraph, op: str, payload: Dict[str, Any]) -> None:
    if op == "upsert_vertex":
        graph.upsert_vertex(
            payload["id"],
            payload["label"],
            pk=payload.get("pk") or payload["id"],
            props=payload.get("props"),
        )
    elif op == "supersede_edge":
        graph.supersede_edge(
            payload["from_id"],
            payload["rel"],
            payload["to_id"],
            valid_from=payload["valid_from"],
            pk=payload.get("pk") or payload["from_id"],
            to_label=payload.get("to_label") or "Entity",
            to_props=payload.get("to_props"),
        )
    else:
        raise ValueError(f"unknown op {op}")


async def flush_graph_outbox(pg: PostgresMemoryStore, graph: CosmosGremlinGraph) -> Dict[str, Any]:
    pending = await pg.list_pending_outbox()
    ok = 0
    failed = 0
    for item in pending:
        payload = item["payload"]
        try:
            await asyncio.to_thread(_apply_op, graph, item["op"], payload)
            await pg.mark_outbox(item["id"], ok=True)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("outbox flush failed for %s", item["id"])
            await pg.mark_outbox(item["id"], ok=False, error=str(exc))
            failed += 1
    return {"processed": ok, "failed": failed, "seen": len(pending)}
