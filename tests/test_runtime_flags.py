"""Tests: runtime_flags.py (added 2026-07-28)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import runtime_flags


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "state.db")


@pytest.mark.asyncio
async def test_effective_falls_back_to_settings_when_no_override(db_path):
    settings = SimpleNamespace(CHASE_COMPOSER_ENABLED=True)
    assert await runtime_flags.effective("CHASE_COMPOSER_ENABLED", settings, db_path) is True

    settings_off = SimpleNamespace(CHASE_COMPOSER_ENABLED=False)
    assert await runtime_flags.effective("CHASE_COMPOSER_ENABLED", settings_off, db_path) is False


@pytest.mark.asyncio
async def test_set_override_takes_precedence_over_settings(db_path):
    settings = SimpleNamespace(CHASE_COMPOSER_ENABLED=False)
    await runtime_flags.set_override("CHASE_COMPOSER_ENABLED", True, db_path)
    assert await runtime_flags.effective("CHASE_COMPOSER_ENABLED", settings, db_path) is True


@pytest.mark.asyncio
async def test_override_persists_and_can_be_flipped_back(db_path):
    await runtime_flags.set_override("CHASE_SMART_ESCALATION_ENABLED", True, db_path)
    assert await runtime_flags.get_override("CHASE_SMART_ESCALATION_ENABLED", db_path) is True

    await runtime_flags.set_override("CHASE_SMART_ESCALATION_ENABLED", False, db_path)
    assert await runtime_flags.get_override("CHASE_SMART_ESCALATION_ENABLED", db_path) is False


@pytest.mark.asyncio
async def test_set_override_rejects_non_whitelisted_flag(db_path):
    with pytest.raises(ValueError):
        await runtime_flags.set_override("CHASE_DRY_RUN", True, db_path)


@pytest.mark.asyncio
async def test_get_override_returns_none_when_never_set(db_path):
    assert await runtime_flags.get_override("CHASE_COMPOSER_ENABLED", db_path) is None
