"""Episodic memory — recent ledger events."""
from __future__ import annotations

from typing import Any, Dict, List


def recall_episodic(events: List[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    facts = []
    for e in events[-limit:]:
        facts.append(
            {
                "layer": "episodic",
                "key": e.get("kind"),
                "value": e.get("detail"),
                "at": e.get("at"),
                "principle": "P6",
            }
        )
    return facts
