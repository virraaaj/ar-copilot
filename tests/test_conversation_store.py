"""Phase 4 tests: ConversationStore. Real SQLite against a temp file."""
from __future__ import annotations

import pytest

from app.channels.teams.conversation_store import ConversationStore


@pytest.fixture
def store(tmp_path) -> ConversationStore:
    return ConversationStore(db_path=str(tmp_path / "conv.db"))


@pytest.mark.asyncio
async def test_record_then_lookup(store: ConversationStore) -> None:
    await store.record("pm@corehelix.ai", "conv-123")

    assert await store.get_conversation_id("pm@corehelix.ai") == "conv-123"


@pytest.mark.asyncio
async def test_lookup_unknown_returns_none(store: ConversationStore) -> None:
    assert await store.get_conversation_id("nobody@corehelix.ai") is None


@pytest.mark.asyncio
async def test_lookup_is_case_insensitive(store: ConversationStore) -> None:
    await store.record("PM@CoreHelix.ai", "conv-1")

    assert await store.get_conversation_id("pm@corehelix.ai") == "conv-1"


@pytest.mark.asyncio
async def test_re_recording_updates_conversation_id(store: ConversationStore) -> None:
    await store.record("pm@corehelix.ai", "conv-old")
    await store.record("pm@corehelix.ai", "conv-new")

    assert await store.get_conversation_id("pm@corehelix.ai") == "conv-new"
