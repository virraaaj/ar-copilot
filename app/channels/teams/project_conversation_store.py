"""
SQLite-backed store mapping a project to its Teams *group* conversation
(added 2026-07-16, replaces per-user DM routing for reach-out — see
PLAN.md §5 Phase 4 note and the 2026-07-16 ideation thread). One project ==
one chat, with all of the project's invoices pooled into it and all of the
project's contacts as members — not a 1:1 DM per PM.

Reverse lookup (conversation_id -> project_number) is what lets bot.py know
which project's invoices to offer when someone types "I want to snooze" in
a project chat, without the incoming activity needing to say so itself.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_conversation_refs (
    project_number TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


class ProjectConversationStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        s = get_settings()
        self._db_path = db_path or s.STATE_DB_PATH
        self._initialized = False

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def record(self, project_number: str, conversation_id: str) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO project_conversation_refs (project_number, conversation_id) VALUES (?, ?) "
                "ON CONFLICT(project_number) DO UPDATE SET conversation_id=excluded.conversation_id, updated_at=datetime('now')",
                (project_number, conversation_id),
            )
            await db.commit()

    async def get_conversation_id(self, project_number: str) -> Optional[str]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT conversation_id FROM project_conversation_refs WHERE project_number = ?", (project_number,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def get_project_number(self, conversation_id: str) -> Optional[str]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT project_number FROM project_conversation_refs WHERE conversation_id = ?", (conversation_id,)
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def list_all(self) -> List[Dict[str, str]]:
        """Every known project-chat mapping -- added 2026-07-16 for
        digest_engine.py's poller, which needs to iterate every project
        that actually has a Teams chat rather than being told one."""
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT project_number, conversation_id FROM project_conversation_refs")
            rows = await cursor.fetchall()
        return [{"project_number": r[0], "conversation_id": r[1]} for r in rows]
