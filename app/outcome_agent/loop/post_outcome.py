"""Post-outcome judge + failed-ask reflexion (P5, P10, P13)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.outcome_agent.store.learning_store import LearningStore


async def judge_due_commitments(
    case: Dict[str, Any],
    learning: LearningStore,
    *,
    now: datetime,
) -> List[Dict[str, Any]]:
    """Classify active payment commitments that are past due."""
    results = []
    commitments = list(case.get("commitments") or [])
    dialogue = case.get("dialogue") or {}
    changed = False
    today = now.date().isoformat()

    for c in commitments:
        if c.get("status") != "active":
            continue
        if c.get("type") != "payment_date":
            continue
        if (c.get("date") or "") > today:
            continue
        # Missed unless world paid
        world = case.get("world") or {}
        if float(world.get("balance_due") or 0) <= 0 or world.get("status") == "paid":
            c["status"] = "kept"
            outcome = "kept"
        else:
            c["status"] = "missed"
            outcome = "missed"
        ask_id = dialogue.get("last_ask_id") or f"ask-{case.get('id')}-{c.get('id')}"
        tactic = dialogue.get("last_ask_tactic") or "confirm_promise"
        rec = await learning.record(
            ask_id=ask_id,
            case_id=case.get("case_id") or "",
            tactic=tactic,
            outcome=outcome,
            objective=(case.get("goals") or {}).get("current_objective", "obtain_commitment"),
            produced_commitment_id=c.get("id"),
            scored_at=now.isoformat(),
        )
        results.append(rec)
        changed = True

        if outcome == "missed":
            failed = list(case.get("failed_asks") or [])
            failed.append(
                {
                    "ask_id": ask_id,
                    "tactic": tactic,
                    "objective": "obtain_commitment",
                    "reason": f"Promise {c.get('date')} missed",
                    "at": now.isoformat(),
                }
            )
            case["failed_asks"] = failed

    if changed:
        case["commitments"] = commitments
    return results


def reflexion_note(case: Dict[str, Any]) -> str:
    failed = case.get("failed_asks") or []
    if not failed:
        return ""
    last = failed[-1]
    goals = case.get("goals") or {}
    return (
        f"Last ask failed ({last.get('tactic')}: {last.get('reason')}) → "
        f"objective changed to {goals.get('current_objective', '…')} (P13)"
    )
