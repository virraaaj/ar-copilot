import pytest

from app.outcome_agent.domain.budgets import (
    consume_postponement,
    consume_unanswered,
    default_budget,
    should_escalate_for_budget,
)
from app.outcome_agent.loop.scheduler import reset_demo, run_agent_tick
from app.outcome_agent.store.case_store import CaseStore
from tests.outcome_agent.conftest import make_settings


def test_budget_exhaustion_flag():
    b = default_budget(max_unanswered=2)
    consume_unanswered(b)
    consume_unanswered(b)
    assert should_escalate_for_budget(b)


def test_budget_exhausted_at_max_postponements():
    b = default_budget(max_postponements=2)
    consume_postponement(b)
    consume_postponement(b)
    assert b.postponements_used >= b.max_postponements
    assert should_escalate_for_budget(b)


def test_budget_not_exhausted_below_max_postponements():
    b = default_budget(max_postponements=2)
    consume_postponement(b)
    assert b.postponements_used < b.max_postponements
    assert not should_escalate_for_budget(b)


def test_budget_exhausted_by_postponements_alone():
    # 2026-09-03: regression guard for the bug where exhausted() ignored
    # postponements_used entirely -- a customer who only ever postpones
    # (never fails to answer, never misses a promise) must still escalate.
    b = default_budget(max_postponements=2)
    consume_postponement(b)
    consume_postponement(b)
    assert b.unanswered_used == 0
    assert b.misses_used == 0
    assert should_escalate_for_budget(b)


@pytest.mark.asyncio
async def test_silence_produces_escalation_dossier(db_path):
    settings = make_settings(STATE_DB_PATH=db_path)
    await reset_demo(db_path)
    store = CaseStore(db_path=db_path)
    case = await store.get_by_invoice("INV-2333")
    assert case
    # Near cap already (2/3); two ticks should escalate
    await run_agent_tick(settings, db_path=db_path, case_row_id=case["id"])
    await run_agent_tick(settings, db_path=db_path, case_row_id=case["id"])
    updated = await store.get(case["id"])
    assert updated["state"] in ("escalation_required", "escalated_to_human") or updated.get("escalation")
    if updated.get("escalation"):
        assert updated["escalation"].get("recommended_human_move")
        assert updated["escalation"].get("autonomy_suppressed") is True
