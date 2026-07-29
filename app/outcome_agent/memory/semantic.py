"""Semantic memory — failed asks + tactic affinities."""
from __future__ import annotations

from typing import Any, Dict, List


def recall_semantic(
    failed_asks: List[Dict[str, Any]], tactic_weights: Dict[str, float]
) -> List[Dict[str, Any]]:
    facts = []
    for fa in failed_asks[-5:]:
        facts.append(
            {
                "layer": "semantic",
                "key": "failed_ask",
                "value": fa,
                "principle": "P13",
            }
        )
    for tactic, weight in sorted(tactic_weights.items(), key=lambda x: x[1]):
        if abs(weight) > 0.01:
            facts.append(
                {
                    "layer": "semantic",
                    "key": "tactic_weight",
                    "value": {"tactic": tactic, "weight": weight},
                    "principle": "P10",
                }
            )
    return facts
