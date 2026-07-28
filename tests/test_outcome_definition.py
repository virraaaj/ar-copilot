"""Tests: outcome_definition.py (added 2026-07-28, spec §6.1)."""
from __future__ import annotations

from app.services.chase_machine import ChaseConfig
from app.services.outcome_definition import OutcomeDefinition


def test_defaults_match_spec_shape():
    od = OutcomeDefinition()

    assert od.use_case == "collections"
    assert od.primary_outcome == "payment_received"
    assert "payment_date" in od.acceptable_commitments
    assert "blocker_resolution_date" in od.acceptable_commitments
    assert "human_escalation_assigned" in od.acceptable_commitments
    assert "closed_paid" in od.terminal_states


def test_from_chase_config_reflects_real_thresholds():
    config = ChaseConfig(max_nudges=5, nudge_interval_days=4)

    od = OutcomeDefinition.from_chase_config(config)

    assert od.default_max_unanswered_outreach == 5
    assert od.default_min_days_between_emails == 4


def test_to_dict_is_json_serializable_shape():
    od = OutcomeDefinition()

    d = od.to_dict()

    assert set(d.keys()) == {
        "use_case", "primary_outcome", "acceptable_commitments", "terminal_states",
        "default_max_unanswered_outreach", "default_min_days_between_emails",
    }
