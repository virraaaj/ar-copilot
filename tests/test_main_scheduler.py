"""
Tests: the background poller scheduler in main.py's lifespan (added
2026-07-17). Every poller function is monkeypatched so no real network
calls happen; these tests only check the scheduler's own wiring -- which
loops start based on which *_POLL_ENABLED flags, and that they actually
tick (not just get constructed and never called).
"""
from __future__ import annotations

import asyncio

import pytest

import app.main as main_module


def _set_common_env(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")


def _reconfigure(monkeypatch) -> None:
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())


def _patch_all_pollers(monkeypatch):
    calls = {"reminders": [], "followups": [], "mirror": [], "digests": []}

    async def fake_reminders(*a, **k):
        calls["reminders"].append(1)
        return 0

    async def fake_followups(*a, **k):
        calls["followups"].append(1)
        return 0

    async def fake_mirror(*a, **k):
        calls["mirror"].append(1)
        return 0

    async def fake_digests(*a, **k):
        calls["digests"].append(1)
        return 0

    monkeypatch.setattr(main_module, "send_due_reminders", fake_reminders)
    monkeypatch.setattr(main_module, "send_due_followups", fake_followups)
    monkeypatch.setattr(main_module, "mirror_new_replies_to_teams", fake_mirror)
    monkeypatch.setattr(main_module, "send_project_digests", fake_digests)
    return calls


@pytest.mark.asyncio
async def test_nothing_runs_when_all_pollers_disabled(monkeypatch):
    monkeypatch.setenv("PROACTIVE_POLL_ENABLED", "false")
    monkeypatch.setenv("DIGEST_POLL_ENABLED", "false")
    _set_common_env(monkeypatch)
    _reconfigure(monkeypatch)
    calls = _patch_all_pollers(monkeypatch)

    async with main_module.lifespan(main_module.app):
        await asyncio.sleep(0.05)

    assert calls == {"reminders": [], "followups": [], "mirror": [], "digests": []}


@pytest.mark.asyncio
async def test_proactive_enabled_runs_reminders_and_followups_not_digests(monkeypatch):
    monkeypatch.setenv("PROACTIVE_POLL_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_POLL_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("FOLLOWUP_POLL_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("DIGEST_POLL_ENABLED", "false")
    _set_common_env(monkeypatch)
    _reconfigure(monkeypatch)
    calls = _patch_all_pollers(monkeypatch)

    async with main_module.lifespan(main_module.app):
        await asyncio.sleep(0.05)

    assert len(calls["reminders"]) >= 1
    assert len(calls["followups"]) >= 1
    assert len(calls["mirror"]) >= 1
    assert calls["digests"] == []


@pytest.mark.asyncio
async def test_digest_enabled_runs_digests_not_reminders(monkeypatch):
    monkeypatch.setenv("PROACTIVE_POLL_ENABLED", "false")
    monkeypatch.setenv("DIGEST_POLL_ENABLED", "true")
    monkeypatch.setenv("DIGEST_POLL_INTERVAL_SECONDS", "0")
    _set_common_env(monkeypatch)
    _reconfigure(monkeypatch)
    calls = _patch_all_pollers(monkeypatch)

    async with main_module.lifespan(main_module.app):
        await asyncio.sleep(0.05)

    assert len(calls["digests"]) >= 1
    assert calls["reminders"] == []
    assert calls["followups"] == []


@pytest.mark.asyncio
async def test_a_failing_tick_does_not_stop_the_loop(monkeypatch):
    """A poller function raising must not kill the whole background loop --
    the next tick should still happen."""
    monkeypatch.setenv("PROACTIVE_POLL_ENABLED", "true")
    monkeypatch.setenv("PROACTIVE_POLL_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("FOLLOWUP_POLL_INTERVAL_SECONDS", "9999")  # keep followups quiet for this test
    monkeypatch.setenv("DIGEST_POLL_ENABLED", "false")
    _set_common_env(monkeypatch)
    _reconfigure(monkeypatch)
    _patch_all_pollers(monkeypatch)

    tick_count = {"n": 0}

    async def flaky_reminders(*a, **k):
        tick_count["n"] += 1
        raise RuntimeError("simulated backend outage")

    monkeypatch.setattr(main_module, "send_due_reminders", flaky_reminders)

    async with main_module.lifespan(main_module.app):
        await asyncio.sleep(0.05)

    assert tick_count["n"] >= 2  # kept ticking despite every call raising
