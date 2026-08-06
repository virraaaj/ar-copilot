"""Build the store bundle used by the live agent loop."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.outcome_agent.mailbox.store import MailboxStore
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.event_ledger import EventLedger
from app.outcome_agent.store.learning_store import LearningStore
from app.outcome_agent.store.semantic_store import SemanticStore
from app.outcome_agent.store.trace_store import TraceStore


@dataclass
class StoreBundle:
    case: CaseStore
    ledger: Any
    facts: Any
    learning: Any
    mailbox: MailboxStore
    traces: TraceStore
    graph: Any
    db_path: str
    mode: str  # sqlite | azure
    backends: Dict[str, str] = field(default_factory=dict)

    def topology(self) -> Dict[str, Any]:
        return {"mode": self.mode, "backends": dict(self.backends), "db_path": self.db_path}


def _azure_enabled(settings, db_path: Optional[str] = None) -> bool:
    mode = (getattr(settings, "OUTCOME_STORE_BACKEND", None) or "auto").lower()
    if mode == "sqlite":
        return False
    if mode == "azure":
        return True
    # auto: DATABASE_URL set, and not an ephemeral/test db_path override
    if not getattr(settings, "DATABASE_URL", None):
        return False
    default_path = getattr(settings, "STATE_DB_PATH", None)
    if db_path and default_path and db_path != default_path:
        return False
    return True


def build_runtime_stores(settings=None, db_path: Optional[str] = None) -> StoreBundle:
    from app.config import get_settings

    settings = settings or get_settings()
    case = CaseStore(db_path=db_path)
    path = case.db_path
    mailbox = MailboxStore(db_path=path)
    traces = TraceStore(db_path=path)

    backends = {
        "operational": "sqlite",
        "case": "sqlite",
        "mailbox": "sqlite",
        "traces": "sqlite",
        "document": "policy_index",
        "context": "runtime",
        "candidates": "runtime",
        "plan": "runtime",
        "draft": "runtime",
        "judgment": "runtime",
        "trigger": "runtime",
        "scheduler": "sqlite",
        "policy": "policy_index",
        "world": "sqlite",
        "learning": "sqlite",
    }

    if not _azure_enabled(settings, db_path=db_path):
        backends.update(
            {
                "episodic": "sqlite",
                "semantic": "sqlite",
                "learning": "sqlite",
                "graph": "sqlite",
            }
        )
        graph = None
        try:
            from app.services.graph_store import GraphStore

            graph = GraphStore(db_path=path)
        except Exception:
            pass
        return StoreBundle(
            case=case,
            ledger=EventLedger(db_path=path),
            facts=SemanticStore(db_path=path),
            learning=LearningStore(db_path=path),
            mailbox=mailbox,
            traces=traces,
            graph=graph,
            db_path=path,
            mode="sqlite",
            backends=backends,
        )

    # Azure path: Postgres for episodic/semantic/learning; Cosmos for graph
    from app.outcome_agent.azure.adapters import (
        AzureEventLedger,
        AzureLearningStore,
        AzureSemanticStore,
        CosmosGraphAdapter,
        get_shared_pg,
        get_shared_gremlin,
        mirror_case_to_postgres,
    )

    pg = get_shared_pg(settings)
    gremlin = get_shared_gremlin(settings)
    backends.update(
        {
            "episodic": "postgres",
            "semantic": "postgres",
            "learning": "postgres",
            "graph": "cosmos_gremlin",
            "facts": "postgres",
        }
    )
    return StoreBundle(
        case=case,
        ledger=AzureEventLedger(pg, case_store=case, mirror=mirror_case_to_postgres),
        facts=AzureSemanticStore(pg, case_store=case, mirror=mirror_case_to_postgres),
        learning=AzureLearningStore(pg),
        mailbox=mailbox,
        traces=traces,
        graph=CosmosGraphAdapter(pg, gremlin),
        db_path=path,
        mode="azure",
        backends=backends,
    )


def annotate_io(backends: Dict[str, str], store: str, key: str, summary: str, payload: Any = None) -> Dict[str, Any]:
    """Build a TraceStep read/write entry with backend provenance."""
    entry: Dict[str, Any] = {
        "store": store,
        "key": key,
        "summary": summary,
        "backend": backends.get(store) or backends.get(store.split(".")[0], "runtime"),
    }
    if payload is not None:
        entry["payload"] = payload
    return entry
