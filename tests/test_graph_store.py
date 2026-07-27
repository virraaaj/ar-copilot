"""Tests: graph_store.py (added 2026-07-25) -- the temporal knowledge
graph. Real SQLite against a temp file, same pattern as
test_chase_store.py."""
from __future__ import annotations

import pytest

from app.services.graph_store import GraphStore


@pytest.fixture
def store(tmp_path) -> GraphStore:
    return GraphStore(db_path=str(tmp_path / "graph.db"))


@pytest.mark.asyncio
async def test_upsert_node_then_get(store: GraphStore) -> None:
    await store.upsert_node("invoice:INV-1", "Invoice", label="INV-1", attributes={"amount": 5000})

    node = await store.get_node("invoice:INV-1")

    assert node["type"] == "Invoice"
    assert node["label"] == "INV-1"
    assert node["attributes"] == {"amount": 5000}


@pytest.mark.asyncio
async def test_upsert_node_is_idempotent_and_updates_fields(store: GraphStore) -> None:
    await store.upsert_node("invoice:INV-1", "Invoice", label="INV-1")
    await store.upsert_node("invoice:INV-1", "Invoice", label="INV-1-updated")

    node = await store.get_node("invoice:INV-1")

    assert node["label"] == "INV-1-updated"


@pytest.mark.asyncio
async def test_get_node_returns_none_when_missing(store: GraphStore) -> None:
    assert await store.get_node("invoice:does-not-exist") is None


@pytest.mark.asyncio
async def test_add_edge_then_list(store: GraphStore) -> None:
    await store.add_edge("customer:1", "CUSTOMER_HAS_INVOICE", "invoice:INV-1")

    edges = await store.list_edges("customer:1")

    assert len(edges) == 1
    assert edges[0]["to_node_id"] == "invoice:INV-1"
    assert edges[0]["relationship"] == "CUSTOMER_HAS_INVOICE"
    assert edges[0]["valid_to"] is None


@pytest.mark.asyncio
async def test_list_edges_direction_to_finds_incoming(store: GraphStore) -> None:
    await store.add_edge("customer:1", "CUSTOMER_HAS_INVOICE", "invoice:INV-1")

    edges = await store.list_edges("invoice:INV-1", direction="to")

    assert len(edges) == 1
    assert edges[0]["from_node_id"] == "customer:1"


@pytest.mark.asyncio
async def test_close_edge_sets_valid_to_and_excludes_from_active_list(store: GraphStore) -> None:
    edge_id = await store.add_edge("invoice:INV-1", "INVOICE_BLOCKED_BY", "blocker:1")

    await store.close_edge(edge_id)

    active = await store.list_edges("invoice:INV-1", active_only=True)
    all_edges = await store.list_edges("invoice:INV-1", active_only=False)
    assert active == []
    assert len(all_edges) == 1
    assert all_edges[0]["valid_to"] is not None


@pytest.mark.asyncio
async def test_supersede_edge_closes_old_and_opens_new_preserving_history(store: GraphStore) -> None:
    """The core spec example: an invoice used to be blocked by X, now it's
    blocked by Y -- the old fact must still be queryable, just inactive."""
    await store.supersede_edge("invoice:INV-1", "INVOICE_BLOCKED_BY", "blocker:approval")

    active = await store.list_edges("invoice:INV-1", relationship="INVOICE_BLOCKED_BY", active_only=True)
    assert len(active) == 1
    assert active[0]["to_node_id"] == "blocker:approval"

    await store.supersede_edge("invoice:INV-1", "INVOICE_BLOCKED_BY", "blocker:cash_flow")

    active_after = await store.list_edges("invoice:INV-1", relationship="INVOICE_BLOCKED_BY", active_only=True)
    all_after = await store.list_edges("invoice:INV-1", relationship="INVOICE_BLOCKED_BY", active_only=False)
    assert len(active_after) == 1
    assert active_after[0]["to_node_id"] == "blocker:cash_flow"
    assert len(all_after) == 2  # the original approval blocker is still there, just closed
    closed = [e for e in all_after if e["to_node_id"] == "blocker:approval"][0]
    assert closed["valid_to"] is not None


@pytest.mark.asyncio
async def test_supersede_edge_only_closes_the_same_relationship(store: GraphStore) -> None:
    """A blocker being superseded must not touch an unrelated relationship
    on the same node (e.g. INVOICE_HAS_COMMITMENT)."""
    await store.add_edge("invoice:INV-1", "INVOICE_HAS_COMMITMENT", "commitment:1")
    await store.supersede_edge("invoice:INV-1", "INVOICE_BLOCKED_BY", "blocker:approval")

    commitment_edges = await store.list_edges("invoice:INV-1", relationship="INVOICE_HAS_COMMITMENT")
    assert len(commitment_edges) == 1
    assert commitment_edges[0]["valid_to"] is None


@pytest.mark.asyncio
async def test_neighborhood_returns_nodes_and_edges_in_both_directions(store: GraphStore) -> None:
    await store.upsert_node("customer:1", "Customer", label="Acme")
    await store.upsert_node("invoice:INV-1", "Invoice", label="INV-1")
    await store.upsert_node("blocker:1", "Blocker", label="Approval pending")
    await store.add_edge("customer:1", "CUSTOMER_HAS_INVOICE", "invoice:INV-1")
    await store.add_edge("invoice:INV-1", "INVOICE_BLOCKED_BY", "blocker:1")

    result = await store.neighborhood("invoice:INV-1")

    assert set(result["nodes"].keys()) == {"customer:1", "invoice:INV-1", "blocker:1"}
    assert len(result["edges"]) == 2


@pytest.mark.asyncio
async def test_edge_carries_source_event_id_and_confidence(store: GraphStore) -> None:
    await store.add_edge(
        "invoice:INV-1", "INVOICE_BLOCKED_BY", "blocker:1", source_event_id="evt-123", confidence=0.8
    )

    edges = await store.list_edges("invoice:INV-1")

    assert edges[0]["source_event_id"] == "evt-123"
    assert edges[0]["confidence"] == 0.8
