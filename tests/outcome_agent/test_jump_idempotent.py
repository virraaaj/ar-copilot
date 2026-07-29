import pytest

from app.outcome_agent.loop.scheduler import jump_sim_clock, reset_demo
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.event_ledger import EventLedger
from tests.outcome_agent.conftest import make_settings


@pytest.mark.asyncio
async def test_jump_emits_date_advanced_once(db_path):
    settings = make_settings(STATE_DB_PATH=db_path)
    await reset_demo(db_path)
    store = CaseStore(db_path=db_path)
    case = await store.get_by_invoice("INV-4821")
    ledger = EventLedger(db_path=db_path)

    await jump_sim_clock("2026-07-28", settings, db_path=db_path)
    await jump_sim_clock("2026-07-28", settings, db_path=db_path)

    events = await ledger.list_for_case(case["id"])
    advanced = [e for e in events if e["kind"] == "date_advanced"]
    keys = [(e.get("detail") or {}).get("jump_key") for e in advanced]
    assert keys.count("date_advanced:2026-07-28") == 1
