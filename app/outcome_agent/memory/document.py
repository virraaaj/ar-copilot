"""Document memory adapter over policy_knowledge."""
from __future__ import annotations

from typing import Any, Dict, List


def recall_policy(query: str, config: Any, top_k: int = 2) -> List[Dict[str, Any]]:
    try:
        from app.services.policy_knowledge import retrieve
    except Exception:
        return []
    docs = retrieve(query, config, top_k=top_k)
    return [
        {
            "layer": "document",
            "key": d.id,
            "value": {"title": d.title, "category": d.category, "excerpt": d.text[:280]},
            "principle": "P6",
        }
        for d in docs
    ]
