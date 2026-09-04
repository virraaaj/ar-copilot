"""Shared concurrency guards for the outcome-agent send tail.

Added 2026-09-04 to close a confirmed race: `run_agent_tick` (the
background poller, scheduler.py) runs as an asyncio task in the same
process/event loop as the web API. A manual AR-aging upload can call
`sync_invoices_to_cases` concurrently, marking an invoice paid and
closing its case. A tick that already fetched that case before the
upload landed then spends seconds awaiting several LLM calls (plan,
draft, judge) with many event-loop yield points -- during that window
the case can go from unpaid to paid-and-closed in the DB while the
tick is still holding a stale in-memory copy. Without a guard, the
tick's pre-send check reads that stale copy, sends a chase email to an
already-paid customer, and then persists its own stale state back over
the DB, reverting the closure.

Two pieces live here so executor.py (tick/poller path) and
traced_loop.py (manual follow-up / reply path) share ONE registry and
ONE terminal-state rule -- they must lock against each other, not just
against themselves, and must agree on what "already closed out" means.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from app.outcome_agent.domain.state_machine import terminal

logger = logging.getLogger(__name__)

# Per-case locks, keyed by case row id. A single module-level dict is
# shared by both send paths (executor.py and traced_loop.py import the
# same `get_case_lock`) so a tick and a manual follow-up on the same
# case serialize against each other across the guard->send->persist
# tail. Deliberately NOT used around the plan/draft/judge LLM calls
# above that region -- an aging upload (or any other tick) must never
# block behind model calls, only behind another in-flight send for the
# same case.
_case_locks: Dict[str, asyncio.Lock] = {}


def get_case_lock(case_id: str) -> asyncio.Lock:
    lock = _case_locks.get(case_id)
    if lock is None:
        lock = asyncio.Lock()
        _case_locks[case_id] = lock
    return lock


# state_machine.terminal() covers paid/closed/disputed/suppressed but
# deliberately not escalated_to_human (that function is about whether
# collections activity should still run, and an escalated case still
# has a human-in-the-loop workflow around it). For THIS guard -- "must a
# stale in-memory tick be forbidden from overwriting what the DB says
# now" -- escalated_to_human is just as final: once a human owns a
# case, a stale tick must not silently return it to automated chasing.
_ALSO_TERMINAL_FOR_PERSIST = {"escalated_to_human"}


def is_db_state_terminal(state: Optional[str]) -> bool:
    """True if `state` is a DB state a stale in-memory copy must never
    overwrite (paid, closed, disputed, suppressed, escalated_to_human)."""
    return bool(state) and (terminal(state) or state in _ALSO_TERMINAL_FOR_PERSIST)


async def guarded_case_update(store, case: Dict[str, Any], **fields: Any) -> None:
    """`store.update()` wrapper that refuses to let a stale in-memory
    `case` resurrect a case the DB has already closed out from under it.

    Re-reads the case's CURRENT DB state first. If that DB state is
    terminal (see `is_db_state_terminal`) and disagrees with the state
    this write is about to apply, drop `state`/`world`/`next_action_at`
    from the write -- the exact fields that would revert the closure --
    and log it clearly. Everything else in `fields` (dialogue,
    commitments, escalation, budget, ...) is still written, so the rest
    of this tick's work is not silently lost.
    """
    fresh = await store.get(case["id"])
    fresh_state = (fresh or {}).get("state")
    in_memory_state = fields.get("state", case.get("state"))
    if is_db_state_terminal(fresh_state) and fresh_state != in_memory_state:
        logger.warning(
            "guarded_case_update: case %s is %r in the DB (terminal) but this write's "
            "in-memory copy has state=%r -- dropping state/world/next_action_at from the "
            "write to avoid reverting the DB's terminal state (likely a concurrent "
            "AR-aging upload closing the case mid-tick)",
            case.get("id"), fresh_state, in_memory_state,
        )
        fields = {k: v for k, v in fields.items() if k not in ("state", "world", "next_action_at")}
    await store.update(case["id"], **fields)
