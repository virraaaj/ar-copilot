import pytest

from app.outcome_agent.loop.scheduler import jump_sim_clock, reset_demo, run_agent_tick
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.learning_store import LearningStore
from tests.outcome_agent.conftest import make_settings


@pytest.mark.asyncio
async def test_missed_promise_writes_weight_delta(db_path):
    settings = make_settings(STATE_DB_PATH=db_path)
    await reset_demo(db_path)
    await jump_sim_clock("2026-07-25", settings, db_path=db_path)
    await run_agent_tick(settings, db_path=db_path)
    learning = LearningStore(db_path=db_path)
    rows = await learning.list_for_case("case-inv-8890")
    assert any(r["outcome"] == "missed" and r["weight_delta"] < 0 for r in rows)
    store = CaseStore(db_path=db_path)
    case = await store.get_by_invoice("INV-8890")
    assert case.get("failed_asks") or case.get("state") == "promise_missed"
