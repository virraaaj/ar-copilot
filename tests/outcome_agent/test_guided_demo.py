"""Coverage for app/outcome_agent/loop/guided_demo.py.

`resolve_jump_target` is pure/sync, so it's tested directly with synthetic
case dicts -- no store, no LLM, no async. The seed/step wiring itself is
covered by test_reset_seeds_three_cases below (hermetic: forces sqlite +
mock LLM mode so it doesn't depend on live infrastructure).
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.outcome_agent.loop.guided_demo import (
    GUIDED_SCENARIOS,
    reset_guided_demo,
    resolve_jump_target,
)


def _mock_settings():
    return get_settings().model_copy(
        update={"OUTCOME_STORE_BACKEND": "sqlite", "OUTCOME_AGENT_LLM_MODE": "mock"}
    )


# --- resolve_jump_target: pure logic, no I/O ---------------------------

def test_jump_target_relative_to_active_commitment():
    case = {"id": "c1", "commitments": [{"type": "follow_up_date", "status": "active", "date": "2026-08-08"}]}
    assert resolve_jump_target(case, {"days_after_commitment": 0}, scenario_id="s", step_index=0) == "2026-08-08"


def test_jump_target_adds_days_after_commitment():
    case = {"id": "c1", "commitments": [{"type": "payment_date", "status": "active", "date": "2026-08-20"}]}
    assert resolve_jump_target(case, {"days_after_commitment": 2}, scenario_id="s", step_index=0) == "2026-08-22"


def test_jump_target_ignores_superseded_commitments():
    # Regression for the cross-scenario clock-contamination bug (2026-08-18):
    # a stale/superseded commitment from earlier in the case's own history
    # must never be picked over the current active one.
    case = {
        "id": "c1",
        "commitments": [
            {"type": "payment_date", "status": "missed", "date": "2026-08-20"},
            {"type": "payment_date", "status": "active", "date": "2026-09-01"},
        ],
    }
    assert resolve_jump_target(case, {"days_after_commitment": 0}, scenario_id="s", step_index=0) == "2026-09-01"


def test_jump_target_explicit_date_wins():
    case = {"id": "c1", "commitments": []}
    assert resolve_jump_target(case, {"date": "2099-01-01"}, scenario_id="s", step_index=0) == "2099-01-01"


def test_jump_target_raises_without_commitment_or_explicit_date():
    case = {"id": "c1", "commitments": []}
    with pytest.raises(ValueError, match="no active dated commitment"):
        resolve_jump_target(case, {}, scenario_id="s", step_index=0)


def test_scenario_jump_beats_no_longer_hardcode_absolute_dates():
    # The actual bug this whole fix addresses: a jump beat with a literal
    # "date" field is exactly what broke when the shared sim clock had
    # already moved past that point from another scenario's own jump.
    # None of the three scenarios should carry one any more.
    for scenario in GUIDED_SCENARIOS:
        for beat in scenario["beats"]:
            if beat["op"] == "jump":
                assert "date" not in beat, (
                    f"{scenario['id']} has a jump beat with a hardcoded absolute date again"
                )
                assert "days_after_commitment" in beat


# --- reset: hermetic end-to-end sanity check ----------------------------

@pytest.mark.asyncio
async def test_reset_seeds_three_cases(tmp_path):
    db = str(tmp_path / "guided.db")
    result = await reset_guided_demo(_mock_settings(), db_path=db)
    assert result["ok"] is True
    assert set(result["cases"].keys()) == {"DEMO-INV-1001", "DEMO-INV-1002", "DEMO-INV-1003"}
