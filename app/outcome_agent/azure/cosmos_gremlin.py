"""Cosmos DB Gremlin adapter for temporal KG edges."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from gremlin_python.driver import client, serializer
from gremlin_python.driver.protocol import GremlinServerError

log = logging.getLogger(__name__)


class CosmosGremlinGraph:
    def __init__(self, host: str, username: str, password: str, port: int = 443) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self._client: Optional[client.Client] = None

    def connect(self) -> None:
        if self._client is not None:
            return
        url = f"wss://{self.host}:{self.port}/"
        kwargs: Dict[str, Any] = {
            "username": self.username,
            "password": self.password,
            "message_serializer": serializer.GraphSONSerializersV2d0(),
        }
        try:
            from gremlin_python.driver.tornado.transport import TornadoTransport

            kwargs["transport_factory"] = lambda: TornadoTransport(read_timeout=60, write_timeout=60)
        except Exception:
            pass
        self._client = client.Client(url, "g", **kwargs)

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("gremlin close ignored: %s", exc)
        self._client = None

    def _submit(self, query: str, bindings: Optional[Dict[str, Any]] = None) -> List[Any]:
        self.connect()
        assert self._client is not None
        cb = self._client.submit(query, bindings or {})
        return cb.all().result()

    def upsert_vertex(self, vertex_id: str, label: str, pk: str, props: Optional[Dict[str, Any]] = None) -> None:
        props = dict(props or {})
        # Drop existing (ignore if missing)
        try:
            self._submit("g.V(vid).has('pk', pk).drop()", {"vid": vertex_id, "pk": pk})
        except GremlinServerError as exc:
            log.debug("drop skipped: %s", exc)

        q = "g.addV(vlabel).property('id', vid).property('pk', pk)"
        bindings: Dict[str, Any] = {"vlabel": label, "vid": vertex_id, "pk": pk}
        for i, (k, v) in enumerate(props.items()):
            if k in ("id", "pk"):
                continue
            key = f"p{i}"
            q += f".property('{k}', {key})"
            bindings[key] = str(v)
        self._submit(q, bindings)

    def add_edge(
        self,
        from_id: str,
        rel: str,
        to_id: str,
        *,
        valid_from: str,
        valid_to: Optional[str] = None,
        edge_id: Optional[str] = None,
        pk: str,
    ) -> None:
        eid = edge_id or f"{from_id}|{rel}|{to_id}|{valid_from}"
        vt = valid_to or ""
        q = (
            "g.V(fromId).has('pk', pk).as('a')"
            ".V(toId).has('pk', pk).as('b')"
            ".addE(rel).from('a').to('b')"
            ".property('id', eid)"
            ".property('valid_from', vf)"
            ".property('valid_to', vt)"
        )
        self._submit(
            q,
            {
                "fromId": from_id,
                "toId": to_id,
                "pk": pk,
                "rel": rel,
                "eid": eid,
                "vf": valid_from,
                "vt": vt,
            },
        )

    def close_active_edges(self, from_id: str, rel: str, valid_to: str, pk: str) -> int:
        try:
            edges = self._submit(
                "g.V(fromId).has('pk', pk).outE(rel).has('valid_to', '')",
                {"fromId": from_id, "rel": rel, "pk": pk},
            )
        except GremlinServerError:
            return 0
        n = 0
        for e in edges:
            try:
                eid = getattr(e, "id", None) or e
                self._submit("g.E(eid).property('valid_to', vt)", {"eid": eid, "vt": valid_to})
                n += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("close edge failed: %s", exc)
        return n

    def supersede_edge(
        self,
        from_id: str,
        rel: str,
        to_id: str,
        *,
        valid_from: str,
        pk: str,
        to_label: str,
        to_props: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.upsert_vertex(from_id, "Invoice", pk=pk, props={"node_label": from_id})
        self.upsert_vertex(to_id, to_label, pk=pk, props=to_props or {"node_label": to_id})
        self.close_active_edges(from_id, rel, valid_from, pk=pk)
        self.add_edge(from_id, rel, to_id, valid_from=valid_from, valid_to=None, pk=pk)

    def neighborhood(self, vertex_id: str, pk: Optional[str] = None) -> Dict[str, Any]:
        pk = pk or vertex_id.split(":")[-1]
        try:
            count = self._submit("g.V(vid).has('pk', pk).count()", {"vid": vertex_id, "pk": pk})
        except GremlinServerError as exc:
            return {"vertex": [], "out_edges": [], "error": str(exc)}
        if not count or int(count[0]) == 0:
            return {"vertex": [], "out_edges": [], "missing": True}
        verts = self._submit(
            "g.V(vid).has('pk', pk).valueMap(true)",
            {"vid": vertex_id, "pk": pk},
        )
        try:
            outs = self._submit(
                "g.V(vid).has('pk', pk).outE().project('label','to','valid_from','valid_to')"
                ".by(label).by(inV().id()).by(values('valid_from')).by(coalesce(values('valid_to'), constant('')))",
                {"vid": vertex_id, "pk": pk},
            )
        except GremlinServerError:
            outs = []
        return {"vertex": verts, "out_edges": outs}

    def healthcheck(self) -> str:
        self._submit("g.V().limit(1).count()")
        return "ok"
