import pytest

from app.outcome_agent.loop.scheduler import run_scenario
from tests.outcome_agent.conftest import make_settings

SCENARIO_IDS = [
    "S1_world_vs_dialogue",
    "S2_commitment_loop",
    "S3_budget_exhaustion",
    "S4_missed_promise_credit",
    "S5_dispute_exit",
    "S6_uncertainty_clarify",
    "S7_critic_blocks_threat",
    "S8_memory_supersession",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
async def test_scenario_pack(scenario_id, db_path):
    settings = make_settings(STATE_DB_PATH=db_path)
    result = await run_scenario(scenario_id, settings, db_path=db_path)
    assert result["scenario_id"] == scenario_id
    assert result["final_case"]
    assert result["steps"]
    # Assert beats must pass when present
    for step in result["steps"]:
        if step.get("op") == "assert":
            assert step.get("ok") is True, f"{scenario_id} failed asserts: {step.get('asserts')}"
