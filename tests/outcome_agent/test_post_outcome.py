"""judge_due_commitments() decides which tactic to "blame" for a missed
promise. These lock in the 2026-08-19 fix (found via live guided-demo
review): dialogue.last_ask_tactic is whatever tactic sent the MOST RECENT
outbound, which is confirm_promise almost every time -- it's exactly what
runs right after a promise comes in (per goals.py's P13 tie-break). But
confirm_promise only acknowledges a commitment the other party already
gave; it never solicited anything, so it can't be "the tactic that
failed." Blaming it anyway permanently blocked it (via failed_asks) and
tanked its learned weight (via oa_tactic_weights) the very first time any
promise missed, defeating the tie-break fix that made confirm_promise
reachable in the first place."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.outcome_agent.loop.post_outcome import judge_due_commitments
from app.outcome_agent.store.learning_store import LearningStore

NOW = datetime(2026, 8, 22, 9, 0, 0)


def _case(*, last_ask_tactic: str) -> dict:
    return {
        "id": "row-1",
        "case_id": "case-1",
        "world": {"balance_due": 42500.0, "status": "open"},
        "dialogue": {"last_ask_id": "ask-1", "last_ask_tactic": last_ask_tactic},
        "goals": {"current_objective": "obtain_commitment"},
        "commitments": [
            {"id": "cmt-1", "type": "payment_date", "status": "active", "date": "2026-08-20"},
        ],
        "failed_asks": [],
    }


@pytest.mark.asyncio
async def test_missed_promise_does_not_blame_confirm_promise(db_path):
    learning = LearningStore(db_path=db_path)
    case = _case(last_ask_tactic="confirm_promise")

    await judge_due_commitments(case, learning, now=NOW)

    assert case["commitments"][0]["status"] == "missed"
    assert case["failed_asks"] == []
    rows = await learning.list_for_case("case-1")
    assert all(r["tactic"] != "confirm_promise" for r in rows)
    weights = await learning.tactic_weights()
    assert weights.get("confirm_promise", 0.0) == 0.0


@pytest.mark.asyncio
async def test_missed_promise_does_not_blame_blocker_ack(db_path):
    learning = LearningStore(db_path=db_path)
    case = _case(last_ask_tactic="blocker_ack")

    await judge_due_commitments(case, learning, now=NOW)

    assert case["failed_asks"] == []
    weights = await learning.tactic_weights()
    assert weights.get("blocker_ack", 0.0) == 0.0


@pytest.mark.asyncio
async def test_missed_promise_still_blames_a_real_ask_tactic(db_path):
    learning = LearningStore(db_path=db_path)
    case = _case(last_ask_tactic="firm_reminder")

    await judge_due_commitments(case, learning, now=NOW)

    assert case["failed_asks"] and case["failed_asks"][0]["tactic"] == "firm_reminder"
    weights = await learning.tactic_weights()
    assert weights.get("firm_reminder", 0.0) < 0
