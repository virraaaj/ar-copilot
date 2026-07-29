"""Link asks → later outcomes (thin wrapper over LearningStore)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.outcome_agent.store.learning_store import LearningStore


async def assign_credit(
    learning: LearningStore,
    *,
    ask_id: str,
    case_id: str,
    tactic: str,
    outcome: str,
    objective: str = "",
    commitment_id: Optional[str] = None,
    scored_at: Optional[str] = None,
) -> Dict[str, Any]:
    return await learning.record(
        ask_id=ask_id,
        case_id=case_id,
        tactic=tactic,
        outcome=outcome,
        objective=objective,
        produced_commitment_id=commitment_id,
        scored_at=scored_at,
    )
