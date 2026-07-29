"""UI/API contract: decision traces carry principles_fired tags."""
import pytest

from app.outcome_agent.domain.types import PRINCIPLES
from app.outcome_agent.loop.scheduler import advance_case_with_reply, reset_demo, run_agent_tick
from app.outcome_agent.store.case_store import CaseStore
from tests.outcome_agent.conftest import make_settings


def test_all_thirteen_principles_defined():
    assert len(PRINCIPLES) == 13
    for i in range(1, 14):
        assert f"P{i}" in PRINCIPLES


@pytest.mark.asyncio
async def test_decision_trace_has_principles_fired(db_path):
    settings = make_settings(STATE_DB_PATH=db_path)
    await reset_demo(db_path)
    store = CaseStore(db_path=db_path)
    case = await store.get_by_invoice("INV-7104")
    await advance_case_with_reply(case["id"], "maybe soon, not sure yet", settings, db_path=db_path)
    await run_agent_tick(settings, db_path=db_path, case_row_id=case["id"])
    updated = await store.get(case["id"])
    traces = await store.list_decision_traces(case["id"])
    assert traces or updated.get("last_decision")
    last = updated.get("last_decision") or traces[-1]
    fired = last.get("principles_fired") or []
    assert "P12" in fired or "P9" in fired or "P8" in fired
