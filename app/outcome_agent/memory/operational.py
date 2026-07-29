"""Operational memory — current case working set."""
from __future__ import annotations

from typing import Any, Dict, List


def recall_operational(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    facts = [
        {
            "layer": "operational",
            "key": "state",
            "value": case.get("state"),
            "principle": "P6",
        },
        {
            "layer": "operational",
            "key": "next_action_at",
            "value": case.get("next_action_at"),
            "principle": "P6",
        },
    ]
    for c in case.get("commitments") or []:
        if c.get("status") == "active":
            facts.append(
                {
                    "layer": "operational",
                    "key": "active_commitment",
                    "value": c,
                    "principle": "P2",
                }
            )
    for b in case.get("blockers") or []:
        if b.get("status") == "open":
            facts.append(
                {
                    "layer": "operational",
                    "key": "open_blocker",
                    "value": b,
                    "principle": "P6",
                }
            )
    return facts
