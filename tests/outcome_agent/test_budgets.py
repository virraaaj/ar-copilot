import pytest

from app.outcome_agent.domain.budgets import consume_unanswered, default_budget, should_escalate_for_budget
from app.outcome_agent.loop.scheduler import reset_demo, run_agent_tick
from app.outcome_agent.store.case_store import CaseStore
from tests.outcome_agent.conftest import make_settings


def test_budget_exhaustion_flag():
    b = default_budget(max_unanswered=2)
    consume_unanswered(b)
    consume_unanswered(b)
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
