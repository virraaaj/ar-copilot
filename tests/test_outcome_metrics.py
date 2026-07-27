"""Tests: outcome_metrics.py (added 2026-07-25) -- the "% of open chases
with a known next commitment" north-star metric from the long-horizon
outcome agent spec. Real SQLite ChaseStore, same pattern as
test_chase_store.py."""
from __future__ import annotations

import pytest

from app.services.chase_store import ChaseStore
from app.services.outcome_metrics import commitment_breakdown, compute_commitment_metric


@pytest.fixture
def store(tmp_path) -> ChaseStore:
    return ChaseStore(db_path=str(tmp_path / "chases.db"))


@pytest.mark.asyncio
async def test_empty_store_has_no_open_chases(store: ChaseStore) -> None:
    metric = await compute_commitment_metric(store)

    assert metric.total_open == 0
    assert metric.known == 0
    assert metric.known_pct == 0.0


@pytest.mark.asyncio
async def test_commitment_tracked_counts_as_known(store: ChaseStore) -> None:
    chase_id = await store.create("case-1", invoice_no="INV-1")
    await store.update(chase_id, state="commitment_tracked", target="pm")

    metric = await compute_commitment_metric(store)

    assert metric.total_open == 1
    assert metric.known == 1
    assert metric.known_pct == 100.0


@pytest.mark.asyncio
async def test_escalated_counts_as_known_human_has_an_owner(store: ChaseStore) -> None:
    chase_id = await store.create("case-1", invoice_no="INV-1")
    await store.update(chase_id, state="escalated", target="pm")

    metric = await compute_commitment_metric(store)

    assert metric.known == 1


@pytest.mark.asyncio
async def test_plain_awaiting_reply_with_no_history_is_unknown(store: ChaseStore) -> None:
    chase_id = await store.create("case-1", invoice_no="INV-1")
    await store.update(chase_id, state="awaiting_pm", target="pm")

    metric = await compute_commitment_metric(store)

    assert metric.total_open == 1
    assert metric.known == 0
    assert metric.known_pct == 0.0


@pytest.mark.asyncio
async def test_checkback_with_a_date_counts_as_known(store: ChaseStore) -> None:
    chase_id = await store.create("case-1", invoice_no="INV-1")
    await store.update(chase_id, state="awaiting_customer", target="customer")
    await store.add_event(chase_id, "checkback_scheduled", {"target": "customer", "followup_date": "2026-08-01"})

    metric = await compute_commitment_metric(store)

    assert metric.known == 1


@pytest.mark.asyncio
async def test_checkback_with_no_date_stays_unknown(store: ChaseStore) -> None:
    """"Let me check on this" with no timeframe given -- still ambiguous,
    unlike a checkback with a concrete follow-up date."""
    chase_id = await store.create("case-1", invoice_no="INV-1")
    await store.update(chase_id, state="awaiting_customer", target="customer")
    await store.add_event(chase_id, "checkback_scheduled", {"target": "customer", "followup_date": None})

    metric = await compute_commitment_metric(store)

    assert metric.known == 0


@pytest.mark.asyncio
async def test_stale_checkback_after_a_new_reply_no_longer_counts(store: ChaseStore) -> None:
    """A checkback date scheduled a while ago, but the conversation has
    since moved on (a new reply came in) -- that old date shouldn't be
    trusted as the current state."""
    chase_id = await store.create("case-1", invoice_no="INV-1")
    await store.update(chase_id, state="awaiting_customer", target="customer")
    await store.add_event(chase_id, "checkback_scheduled", {"target": "customer", "followup_date": "2026-08-01"})
    await store.add_event(chase_id, "outreach_sent", {"target": "customer", "text": "checking in"})
    await store.add_event(chase_id, "reply_received", {"target": "customer", "text": "still not sure"})

    metric = await compute_commitment_metric(store)

    assert metric.known == 0


@pytest.mark.asyncio
async def test_paused_is_excluded_from_the_denominator(store: ChaseStore) -> None:
    chase_id = await store.create("case-1", invoice_no="INV-1")
    await store.update(chase_id, state="paused", target="pm")

    metric = await compute_commitment_metric(store)

    assert metric.total_open == 0


@pytest.mark.asyncio
async def test_closed_chases_are_excluded_from_the_denominator(store: ChaseStore) -> None:
    chase_id = await store.create("case-1", invoice_no="INV-1")
    await store.update(chase_id, state="closed_paid", target="pm")

    metric = await compute_commitment_metric(store)

    assert metric.total_open == 0


@pytest.mark.asyncio
async def test_mixed_portfolio_computes_correct_percentage(store: ChaseStore) -> None:
    known1 = await store.create("case-1", invoice_no="INV-1")
    await store.update(known1, state="commitment_tracked", target="pm")
    known2 = await store.create("case-2", invoice_no="INV-2")
    await store.update(known2, state="escalated", target="pm")
    unknown1 = await store.create("case-3", invoice_no="INV-3")
    await store.update(unknown1, state="awaiting_pm", target="pm")
    unknown2 = await store.create("case-4", invoice_no="INV-4")
    await store.update(unknown2, state="pending", target=None)

    metric = await compute_commitment_metric(store)

    assert metric.total_open == 4
    assert metric.known == 2
    assert metric.unknown == 2
    assert metric.known_pct == 50.0


@pytest.mark.asyncio
async def test_breakdown_lists_each_open_chase_with_its_known_flag(store: ChaseStore) -> None:
    known = await store.create("case-1", invoice_no="INV-1", project_number="PN-1")
    await store.update(known, state="commitment_tracked", target="pm")
    unknown = await store.create("case-2", invoice_no="INV-2", project_number="PN-2")
    await store.update(unknown, state="awaiting_pm", target="pm")

    rows = await commitment_breakdown(store)

    by_case = {r["case_id"]: r for r in rows}
    assert by_case["case-1"]["known_commitment"] is True
    assert by_case["case-1"]["invoice_no"] == "INV-1"
    assert by_case["case-2"]["known_commitment"] is False
