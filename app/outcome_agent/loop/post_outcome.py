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
        last_ask_tactic = dialogue.get("last_ask_tactic")
        # confirm_promise (and blocker_ack) only acknowledge a commitment the
        # other party already gave -- they don't solicit anything, so they
        # can't be "the tactic that failed" when that commitment later
        # misses. dialogue.last_ask_tactic is whatever tactic sent the MOST
        # RECENT outbound, which is confirm_promise almost every time (it's
        # exactly what runs right after a promise comes in, per goals.py's
        # P13 tie-break) -- so blaming it here silently poisoned
        # confirm_promise's learned weight and failed_asks membership on
        # every single missed promise, system-wide. Found 2026-08-19 live:
        # right after the tie-break fix let confirm_promise actually get
        # selected, this defeated it again by permanently blocking it the
        # first time any promise missed, forcing every later re-engagement
        # into clarify_ask's confirmatory phrasing instead of a clean
        # acknowledgment. Recording under a neutral tag keeps the kept/missed
        # stats accurate without punishing a real, reusable tactic.
        blame_tactic = last_ask_tactic not in ("confirm_promise", "blocker_ack")
        tactic = last_ask_tactic if (last_ask_tactic and blame_tactic) else "no_ask_tactic"
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

        if outcome == "missed" and blame_tactic:
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
    goals = case.get("goals") or {}
    if failed:
        last = failed[-1]
        return (
            f"Last ask failed ({last.get('tactic')}: {last.get('reason')}) → "
            f"objective changed to {goals.get('current_objective', '…')} (P13)"
        )
    # No tactic to blame doesn't mean nothing happened -- an
    # acknowledgment-only miss (see judge_due_commitments) still leaves a
    # missed commitment on record; surface that instead of going silent.
    for c in reversed(case.get("commitments") or []):
        if c.get("status") == "missed":
            return (
                f"Promise {c.get('date')} missed (last touch only acknowledged it, "
                f"nothing to blame) → objective changed to "
                f"{goals.get('current_objective', '…')} (P13)"
            )
    return ""
