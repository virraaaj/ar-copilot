"""
Temporal knowledge graph (added 2026-07-25, long-horizon outcome agent
spec §6.8). Tracks entities (Invoice, Customer, Blocker, Commitment, ...)
and the *dated* relationships between them -- the thing chase_store.py's
flat `state`/`blocker_type`/`promised_date` columns can't express: that an
invoice used to be blocked by an approval, and that blocker was later
replaced by a payment promise, without losing the history of the earlier
fact.

Two tables, same aiosqlite-against-STATE_DB_PATH pattern as chase_store.py:

  - graph_nodes: one row per entity. `id` is caller-chosen and stable
    (e.g. "invoice:INV-4821", "blocker:<chase_id>:1") so upserting is
    idempotent -- the same real-world entity always maps to the same node
    even if it's touched from multiple call sites.
  - graph_edges: one row per *version* of a relationship. `valid_to IS
    NULL` means "still true now"; closing an edge (setting valid_to)
    instead of deleting it is what preserves history -- superseded facts
    stay queryable, just no longer "active".

`supersede_edge()` is the one operation that encodes the spec's worked
example directly: close whatever active edge currently exists for
(from_node, relationship), then open a new one. Everything else
(add_edge, list_edges) is a thin, general-purpose layer on top; this
module has no opinion about collections specifically -- chase_engine.py
is the only caller that knows *when* to write a blocker/commitment edge,
same separation of "pure-ish domain logic" vs "the caller decides when
to call it" used throughout this codebase.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from app.config import get_settings

# Node types the spec calls out (§6.8) -- not enforced as a hard enum
# (a caller-chosen id like "invoice:INV-1" already encodes the type in
# practice), but documented here since it's the vocabulary the UI and any
# future writer should stick to.
NODE_TYPES = (
    "Customer", "Contact", "Invoice", "PurchaseOrder", "Payment", "Dispute",
    "Blocker", "Commitment", "InternalOwner", "CommunicationThread", "Policy",
)

# Relationships the spec calls out (§6.8) -- same non-enforced documentation
# role as NODE_TYPES.
RELATIONSHIPS = (
    "CUSTOMER_HAS_INVOICE", "CUSTOMER_HAS_CONTACT", "CONTACT_RESPONSIBLE_FOR",
    "INVOICE_HAS_PURCHASE_ORDER", "INVOICE_BLOCKED_BY", "BLOCKER_OWNED_BY",
    "INVOICE_HAS_COMMITMENT", "COMMITMENT_SUPERSEDES", "INVOICE_HAS_DISPUTE",
    "CONTACT_PREFERS_CHANNEL", "CUSTOMER_HAS_INTERNAL_OWNER", "THREAD_REFERENCES_INVOICE",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    label       TEXT,
    attributes  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS graph_edges (
    id              TEXT PRIMARY KEY,
    from_node_id    TEXT NOT NULL,
    relationship    TEXT NOT NULL,
    to_node_id      TEXT NOT NULL,
    valid_from      TEXT NOT NULL,
    valid_to        TEXT,
    observed_at     TEXT NOT NULL,
    source_event_id TEXT,
    confidence      REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_graph_edges_from ON graph_edges(from_node_id, relationship);
CREATE INDEX IF NOT EXISTS idx_graph_edges_to ON graph_edges(to_node_id, relationship);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class GraphStore:
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

    # ---- nodes --------------------------------------------------------

    async def upsert_node(
        self, node_id: str, node_type: str, label: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Idempotent -- safe to call every time an entity is touched, not
        just the first time it's seen (e.g. an Invoice node's label might
        need refreshing as more is learned about it)."""
        await self._ensure_schema()
        now = _now_iso()
        attrs_json = json.dumps(attributes) if attributes is not None else None
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO graph_nodes (id, type, label, attributes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "  type = excluded.type, "
                "  label = COALESCE(excluded.label, graph_nodes.label), "
                "  attributes = COALESCE(excluded.attributes, graph_nodes.attributes), "
                "  updated_at = excluded.updated_at",
                (node_id, node_type, label, attrs_json, now, now),
            )
            await db.commit()

    async def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM graph_nodes WHERE id = ?", (node_id,))
            row = await cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        d["attributes"] = json.loads(d["attributes"]) if d["attributes"] else None
        return d

    # ---- edges ----------------------------------------------------------

    async def add_edge(
        self, from_node_id: str, relationship: str, to_node_id: str,
        valid_from: Optional[str] = None, observed_at: Optional[str] = None,
        source_event_id: Optional[str] = None, confidence: float = 1.0,
    ) -> str:
        await self._ensure_schema()
        now = _now_iso()
        edge_id = str(uuid.uuid4())
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO graph_edges "
                "(id, from_node_id, relationship, to_node_id, valid_from, valid_to, observed_at, source_event_id, confidence) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)",
                (edge_id, from_node_id, relationship, to_node_id, valid_from or now, observed_at or now,
                 source_event_id, confidence),
            )
            await db.commit()
        return edge_id

    async def close_edge(self, edge_id: str, valid_to: Optional[str] = None) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE graph_edges SET valid_to = ? WHERE id = ? AND valid_to IS NULL",
                (valid_to or _now_iso(), edge_id),
            )
            await db.commit()

    async def supersede_edge(
        self, from_node_id: str, relationship: str, to_node_id: str,
        observed_at: Optional[str] = None, source_event_id: Optional[str] = None,
        confidence: float = 1.0,
    ) -> str:
        """Closes whatever active edge(s) currently exist for
        (from_node_id, relationship) -- regardless of what they point to --
        then opens a new one pointing at to_node_id. This is the operation
        that encodes "an invoice used to be blocked by X, now it's blocked
        by Y" (or "now has a commitment instead") without losing the old
        fact: the old edge's valid_to is set, not deleted, so
        list_edges(from_node_id, relationship, active_only=False) still
        shows it."""
        await self._ensure_schema()
        now = _now_iso()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE graph_edges SET valid_to = ? "
                "WHERE from_node_id = ? AND relationship = ? AND valid_to IS NULL",
                (now, from_node_id, relationship),
            )
            await db.commit()
        return await self.add_edge(
            from_node_id, relationship, to_node_id, valid_from=now,
            observed_at=observed_at, source_event_id=source_event_id, confidence=confidence,
        )

    async def list_edges(
        self, node_id: str, relationship: Optional[str] = None, active_only: bool = True,
        direction: str = "from",
    ) -> List[Dict[str, Any]]:
        """`direction`: "from" finds edges where node_id is the source,
        "to" finds edges where it's the target, "both" finds either."""
        await self._ensure_schema()
        column = {"from": "from_node_id", "to": "to_node_id"}.get(direction)
        clauses = [f"{column} = ?"] if column else ["(from_node_id = ? OR to_node_id = ?)"]
        params: List[Any] = [node_id, node_id] if column is None else [node_id]
        if relationship:
            clauses.append("relationship = ?")
            params.append(relationship)
        if active_only:
            clauses.append("valid_to IS NULL")
        where = " AND ".join(clauses)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                f"SELECT * FROM graph_edges WHERE {where} ORDER BY valid_from ASC", params
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def neighborhood(self, node_id: str, active_only: bool = True) -> Dict[str, Any]:
        """Everything connected to a node (both directions), plus the
        node records for every entity involved -- backs a UI panel that
        wants to render "this invoice's relationships" in one call
        without N+1 lookups."""
        await self._ensure_schema()
        edges = await self.list_edges(node_id, active_only=active_only, direction="both")
        node_ids = {node_id}
        for e in edges:
            node_ids.add(e["from_node_id"])
            node_ids.add(e["to_node_id"])
        nodes = {}
        for nid in node_ids:
            node = await self.get_node(nid)
            if node:
                nodes[nid] = node
        return {"nodes": nodes, "edges": edges}
