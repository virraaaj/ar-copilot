from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

# Settings() requires Azure keys; set dummy env for any accidental get_settings().
os.environ.setdefault("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
os.environ.setdefault("AZURE_OPENAI_KEY", "test-key-not-real")

from app.outcome_agent.store.case_store import CaseStore


def make_settings(**overrides) -> SimpleNamespace:
    base = dict(
        OUTCOME_AGENT_ENABLED=True,
        OUTCOME_AGENT_DRY_RUN=True,
        OUTCOME_AGENT_MAX_SENDS_PER_TICK=10,
        OUTCOME_AGENT_MAX_NUDGES=3,
        OUTCOME_AGENT_MAX_MISSED_COMMITMENTS=3,
        OUTCOME_AGENT_MAX_POSTPONEMENTS=3,
        OUTCOME_AGENT_NUDGE_INTERVAL_DAYS=3,
        OUTCOME_AGENT_MIN_HOURS_BETWEEN_TOUCHES=72,
        OUTCOME_AGENT_TO_ADDRESS_ALLOWLIST="",
        CHASE_ENABLED=False,
        CHASE_DRY_RUN=True,
        CHASE_MAX_NUDGES=3,
        CHASE_MAX_MISSED_COMMITMENTS=3,
        CHASE_MAX_POSTPONEMENTS=3,
        CHASE_NUDGE_INTERVAL_DAYS=3,
        CHASE_MIN_HOURS_BETWEEN_TOUCHES=72,
        CHASE_TO_ADDRESS_ALLOWLIST="",
        CHASE_MAX_SENDS_PER_TICK=10,
        STATE_DB_PATH=":memory:",
    )
    base.update(overrides)
    s = SimpleNamespace(**base)
    s.outcome_agent_to_address_allowlist = []
    s.chase_to_address_allowlist = []
    s.outcome_agent_enabled_effective = True
    s.outcome_agent_dry_run_effective = True
    return s


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "oa.db")


@pytest.fixture
def case_store(db_path) -> CaseStore:
    return CaseStore(db_path=db_path)
