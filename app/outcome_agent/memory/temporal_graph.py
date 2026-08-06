"""Thin adapter over GraphStore (P6 supersession)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


async def recall_graph(graph_store, invoice_no: str) -> List[Dict[str, Any]]:
    if graph_store is None or not invoice_no:
        return []
    node_id = f"invoice:{invoice_no}"
    try:
        neighborhood = await graph_store.neighborhood(node_id, active_only=False)
    except Exception:
        return []
    facts = []
    for edge in neighborhood.get("edges") or []:
        active = edge.get("valid_to") in (None, "", "null")
        facts.append(
            {
                "layer": "temporal_graph",
                "key": edge.get("relationship"),
                "value": {
                    "from": edge.get("from_node_id"),
                    "to": edge.get("to_node_id"),
                    "active": active,
                    "valid_to": edge.get("valid_to"),
                },
                "principle": "P6",
                "superseded": not active,
            }
        )
    return facts


async def supersede_blocker_with_commitment(
    graph_store,
    invoice_no: str,
    blocker_id: str,
    commitment_id: str,
    commitment_attrs: Optional[Dict[str, Any]] = None,
) -> None:
    if graph_store is None:
        return
    inv = f"invoice:{invoice_no}"
    blk = f"blocker:{blocker_id}"
    cmt = f"commitment:{commitment_id}"
    await graph_store.upsert_node(cmt, "Commitment", attributes=commitment_attrs or {})
    # Close active blocker edges
    edges = await graph_store.list_edges(inv, relationship="INVOICE_BLOCKED_BY", active_only=True)
    for e in edges:
        if e.get("to_node_id") == blk or True:
            await graph_store.close_edge(e["id"])
    await graph_store.add_edge(inv, "INVOICE_HAS_COMMITMENT", cmt)
    try:
        await graph_store.add_edge(cmt, "COMMITMENT_SUPERSEDES", blk)
    except Exception:
        pass
