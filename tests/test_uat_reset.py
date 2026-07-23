"""Tests: uat_reset.py (added 2026-07-23). Mocks asyncpg.connect -- this
runs in every environment, including CI where no real Postgres exists."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services import uat_reset


class FakeConn:
    def __init__(self, tables: list[str]) -> None:
        self.tables = tables
        self.executed: list[str] = []
        self.closed = False

    async def fetch(self, query: str):
        return [{"tablename": t} for t in self.tables]

    async def execute(self, query: str):
        self.executed.append(query)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_wipe_uat_data_excludes_keep_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeConn(["invoices", "projects", "users", "default_project_contacts", "schema_migrations"])
    monkeypatch.setattr(uat_reset.asyncpg, "connect", AsyncMock(return_value=fake))

    result = await uat_reset.wipe_uat_data("postgresql://fake/db")

    assert result["wiped_tables"] == ["invoices", "projects"]
    assert result["kept_tables"] == ["default_project_contacts", "schema_migrations", "users"]
    assert len(fake.executed) == 1
    assert "TRUNCATE TABLE" in fake.executed[0]
    assert '"invoices"' in fake.executed[0]
    assert '"projects"' in fake.executed[0]
    assert '"users"' not in fake.executed[0]
    assert "CASCADE" in fake.executed[0]
    assert fake.closed is True


@pytest.mark.asyncio
async def test_wipe_uat_data_closes_connection_even_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeConn(["invoices"])

    async def failing_fetch(query: str):
        raise RuntimeError("boom")

    fake.fetch = failing_fetch  # type: ignore[method-assign]
    monkeypatch.setattr(uat_reset.asyncpg, "connect", AsyncMock(return_value=fake))

    with pytest.raises(RuntimeError):
        await uat_reset.wipe_uat_data("postgresql://fake/db")

    assert fake.closed is True


@pytest.mark.asyncio
async def test_wipe_uat_data_noop_when_nothing_to_wipe(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeConn(["users", "schema_migrations"])
    monkeypatch.setattr(uat_reset.asyncpg, "connect", AsyncMock(return_value=fake))

    result = await uat_reset.wipe_uat_data("postgresql://fake/db")

    assert result["wiped_tables"] == []
    assert fake.executed == []
