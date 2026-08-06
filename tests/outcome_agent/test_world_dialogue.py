import pytest

from app.outcome_agent.domain.dialogue_model import DialogueSnapshot
from app.outcome_agent.domain.world_model import WorldSnapshot
from app.outcome_agent.loop.scheduler import advance_case_with_reply, reset_demo
from tests.outcome_agent.conftest import make_settings


def test_conflict_detection():
    world = WorldSnapshot(
        invoice_no="INV-1", case_id="c1", balance_due=100, status="open"
    )
    dialogue = DialogueSnapshot(customer_claimed_paid=True)
    assert dialogue.conflicts_with_world_unpaid(world.conflicts_with_paid_claim())


@pytest.mark.asyncio
async def test_paid_claim_does_not_mark_paid(db_path):
    settings = make_settings(STATE_DB_PATH=db_path)
    await reset_demo(db_path)
    from app.outcome_agent.store.case_store import CaseStore

    store = CaseStore(db_path=db_path)
    case = await store.get_by_invoice("INV-7104")
    assert case
    await advance_case_with_reply(case["id"], "we already paid this invoice", settings, db_path=db_path)
    updated = await store.get(case["id"])
    assert float((updated["world"] or {}).get("balance_due") or 0) > 0
    assert updated["state"] != "paid"
    goals = updated.get("goals") or {}
    decision = updated.get("last_decision") or {}
    selected = decision.get("selected_action") or {}
    assert (
        goals.get("current_objective") == "verify_payment"
        or selected.get("tactic") == "verify_payment_ask"
        or selected.get("objective") == "verify_payment"
    )
