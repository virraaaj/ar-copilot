"""Tests: ChaseStore (added 2026-07-20, Phase C0). Real SQLite against a
temp file, same pattern as test_followup_store.py / test_project_
conversation_store.py."""
from __future__ import annotations

import pytest

from app.services.chase_store import ChaseError, ChaseStore


@pytest.fixture
def store(tmp_path) -> ChaseStore:
    return ChaseStore(db_path=str(tmp_path / "chases.db"))


@pytest.mark.asyncio
async def test_create_and_get(store: ChaseStore) -> None:
    chase_id = await store.create("case-1", case_key="V2-AUTO-1", invoice_no="INV-1", project_number="PN-1")

    chase = await store.get(chase_id)
    assert chase["case_id"] == "case-1"
    assert chase["case_key"] == "V2-AUTO-1"
    assert chase["invoice_no"] == "INV-1"
    assert chase["state"] == "pending"
    assert chase["missed_count"] == 0
    assert chase["nudge_count"] == 0
    assert chase["subject_token"].startswith("AR-")


@pytest.mark.asyncio
async def test_create_logs_an_event(store: ChaseStore) -> None:
    chase_id = await store.create("case-1")

    events = await store.list_events(chase_id)
    assert len(events) == 1
    assert events[0]["kind"] == "created"


@pytest.mark.asyncio
async def test_second_open_chase_for_same_case_is_refused(store: ChaseStore) -> None:
    await store.create("case-1")

    with pytest.raises(ChaseError):
        await store.create("case-1")


@pytest.mark.asyncio
async def test_new_chase_allowed_after_prior_one_closed(store: ChaseStore) -> None:
    first = await store.create("case-1")
    await store.update(first, state="closed_paid")

    second = await store.create("case-1")

    assert second != first
    assert await store.get_open_for_case("case-1") is not None
    assert (await store.get_open_for_case("case-1"))["id"] == second


@pytest.mark.asyncio
async def test_get_open_for_case_returns_none_when_no_chase(store: ChaseStore) -> None:
    assert await store.get_open_for_case("case-unknown") is None


@pytest.mark.asyncio
async def test_get_by_subject_token(store: ChaseStore) -> None:
    chase_id = await store.create("case-1")
    chase = await store.get(chase_id)

    found = await store.get_by_subject_token(chase["subject_token"])
    assert found["id"] == chase_id


@pytest.mark.asyncio
async def test_subject_tokens_are_unique(store: ChaseStore) -> None:
    ids = [await store.create(f"case-{i}") for i in range(20)]
    chases = [await store.get(i) for i in ids]
    tokens = {c["subject_token"] for c in chases}
    assert len(tokens) == 20


@pytest.mark.asyncio
async def test_update_bumps_updated_at_and_persists_fields(store: ChaseStore) -> None:
    chase_id = await store.create("case-1")
    before = await store.get(chase_id)

    await store.update(chase_id, state="awaiting_pm", target="pm", nudge_count=1)

    after = await store.get(chase_id)
    assert after["state"] == "awaiting_pm"
    assert after["target"] == "pm"
    assert after["nudge_count"] == 1
    assert after["updated_at"] >= before["updated_at"]


@pytest.mark.asyncio
async def test_list_due_only_returns_actionable_states_past_next_action_at(store: ChaseStore) -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    past = (now - timedelta(days=1)).isoformat()
    future = (now + timedelta(days=1)).isoformat()

    due_id = await store.create("case-due", next_action_at=past)
    not_due_id = await store.create("case-not-due", next_action_at=future)
    paused_id = await store.create("case-paused", next_action_at=past)
    await store.update(paused_id, state="paused")
    escalated_id = await store.create("case-escalated", next_action_at=past)
    await store.update(escalated_id, state="escalated")

    due = await store.list_due()
    due_ids = {c["id"] for c in due}

    assert due_id in due_ids
    assert not_due_id not in due_ids
    assert paused_id not in due_ids
    assert escalated_id not in due_ids


@pytest.mark.asyncio
async def test_list_all_filters_by_state(store: ChaseStore) -> None:
    a = await store.create("case-a")
    b = await store.create("case-b")
    await store.update(b, state="escalated")

    escalated = await store.list_all(state="escalated")
    assert [c["id"] for c in escalated] == [b]

    everything = await store.list_all()
    assert {c["id"] for c in everything} == {a, b}


@pytest.mark.asyncio
async def test_list_all_filters_by_case_id(store: ChaseStore) -> None:
    """InvoiceDetail's per-invoice chase lookup (added 2026-07-23) -- finds
    just the one chase for a given invoice/case without fetching every
    chase in the system."""
    a = await store.create("case-a")
    await store.create("case-b")

    found = await store.list_all(case_id="case-a")
    assert [c["id"] for c in found] == [a]

    assert await store.list_all(case_id="no-such-case") == []


@pytest.mark.asyncio
async def test_add_event_and_list_events_preserves_detail(store: ChaseStore) -> None:
    chase_id = await store.create("case-1")

    await store.add_event(chase_id, "outreach_sent", {"target": "pm", "text": "hello"})

    events = await store.list_events(chase_id)
    outreach = [e for e in events if e["kind"] == "outreach_sent"][0]
    assert outreach["detail"] == {"target": "pm", "text": "hello"}


@pytest.mark.asyncio
async def test_mail_dedupe(store: ChaseStore) -> None:
    chase_id = await store.create("case-1")

    assert await store.is_mail_processed("msg-1") is False

    await store.mark_mail_processed("msg-1", chase_id)

    assert await store.is_mail_processed("msg-1") is True
    # idempotent -- marking twice doesn't raise
    await store.mark_mail_processed("msg-1", chase_id)


# ---- token tracking (added 2026-07-22) -------------------------------------


@pytest.mark.asyncio
async def test_new_chase_starts_with_zero_tokens(store: ChaseStore) -> None:
    chase_id = await store.create("case-1")

    chase = await store.get(chase_id)

    assert chase["total_tokens_used"] == 0


@pytest.mark.asyncio
async def test_increment_tokens_accumulates(store: ChaseStore) -> None:
    chase_id = await store.create("case-1")

    await store.increment_tokens(chase_id, 100)
    await store.increment_tokens(chase_id, 50)

    chase = await store.get(chase_id)
    assert chase["total_tokens_used"] == 150


@pytest.mark.asyncio
async def test_increment_tokens_zero_or_negative_is_a_noop(store: ChaseStore) -> None:
    chase_id = await store.create("case-1")

    await store.increment_tokens(chase_id, 0)
    await store.increment_tokens(chase_id, -5)

    chase = await store.get(chase_id)
    assert chase["total_tokens_used"] == 0


@pytest.mark.asyncio
async def test_increment_tokens_accumulates_the_prompt_completion_split(store: ChaseStore) -> None:
    """Added 2026-07-28: total_tokens_used is unchanged (still the sum,
    same number the UI displays), but a TokenUsage-shaped `tokens` (an
    int subclass carrying .prompt/.completion) also accumulates into two
    dedicated columns."""
    from app.services.azure_openai import TokenUsage

    chase_id = await store.create("case-1")

    await store.increment_tokens(chase_id, TokenUsage(100, prompt=70, completion=30))
    await store.increment_tokens(chase_id, TokenUsage(50, prompt=20, completion=30))

    chase = await store.get(chase_id)
    assert chase["total_tokens_used"] == 150
    assert chase["prompt_tokens_used"] == 90
    assert chase["completion_tokens_used"] == 60


@pytest.mark.asyncio
async def test_increment_tokens_with_a_bare_int_leaves_split_at_zero(store: ChaseStore) -> None:
    """A plain int (a test double, or any caller that hasn't been updated
    to pass a TokenUsage) has no .prompt/.completion -- must not crash,
    just contribute nothing to the split columns."""
    chase_id = await store.create("case-1")

    await store.increment_tokens(chase_id, 100)

    chase = await store.get(chase_id)
    assert chase["total_tokens_used"] == 100
    assert chase["prompt_tokens_used"] == 0
    assert chase["completion_tokens_used"] == 0


@pytest.mark.asyncio
async def test_migration_adds_column_to_a_pre_existing_db_without_it(tmp_path) -> None:
    """Regression test for the total_tokens_used migration: a DB whose
    chases table was created before this column existed must not break
    when a newer ChaseStore opens it."""
    import aiosqlite

    db_path = str(tmp_path / "old.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE chases (
                id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                case_key TEXT,
                invoice_no TEXT,
                project_number TEXT,
                subject_token TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                target TEXT,
                pm_email TEXT,
                customer_email TEXT,
                promised_date TEXT,
                promised_by TEXT,
                missed_count INTEGER NOT NULL DEFAULT 0,
                nudge_count INTEGER NOT NULL DEFAULT 0,
                clarify_count INTEGER NOT NULL DEFAULT 0,
                last_outreach_at TEXT,
                next_action_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        await db.commit()

    store = ChaseStore(db_path=db_path)
    chase_id = await store.create("case-1")
    await store.increment_tokens(chase_id, 42)

    chase = await store.get(chase_id)
    assert chase["total_tokens_used"] == 42
