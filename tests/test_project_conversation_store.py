"""Tests: ProjectConversationStore (project-level Teams chats, added 2026-07-16).
Real SQLite against a temp file."""
from __future__ import annotations

import pytest

from app.channels.teams.project_conversation_store import ProjectConversationStore


@pytest.fixture
def store(tmp_path) -> ProjectConversationStore:
    return ProjectConversationStore(db_path=str(tmp_path / "conv.db"))


@pytest.mark.asyncio
async def test_record_then_lookup(store: ProjectConversationStore) -> None:
    await store.record("PN-1", "conv-123")

    assert await store.get_conversation_id("PN-1") == "conv-123"


@pytest.mark.asyncio
async def test_lookup_unknown_returns_none(store: ProjectConversationStore) -> None:
    assert await store.get_conversation_id("PN-unknown") is None


@pytest.mark.asyncio
async def test_re_recording_updates_conversation_id(store: ProjectConversationStore) -> None:
    await store.record("PN-1", "conv-old")
    await store.record("PN-1", "conv-new")

    assert await store.get_conversation_id("PN-1") == "conv-new"


@pytest.mark.asyncio
async def test_reverse_lookup_by_conversation_id(store: ProjectConversationStore) -> None:
    await store.record("PN-1", "conv-123")

    assert await store.get_project_number("conv-123") == "PN-1"


@pytest.mark.asyncio
async def test_reverse_lookup_unknown_returns_none(store: ProjectConversationStore) -> None:
    assert await store.get_project_number("conv-unknown") is None
