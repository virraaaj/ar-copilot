"""
SQLite-backed store mapping a PM's identity to their Teams conversation
reference (PLAN.md §5 Phase 4). Mirrors the earlier bot's
conversation_store.py pattern.

Keyed on email/UPN (not an AAD object id) — consistent with identity.py's
ADMIN_UPNS model, and with how project_contacts already identifies a PM
(email is always present; aad_user_id is only sometimes set — see the
dev-sachin merge notes on Teams-interop calling). A DM is only possible once
this PM has messaged the bot once, or been installed org-wide — a real
Teams platform constraint, not a limitation of this store — so a row here
only ever exists because a real incoming activity provided it.
"""
from __future__ import annotations

from typing import Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS teams_conversation_refs (
    user_email TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


class ConversationStore:
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

    async def record(self, user_email: str, conversation_id: str) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO teams_conversation_refs (user_email, conversation_id) VALUES (?, ?) "
                "ON CONFLICT(user_email) DO UPDATE SET conversation_id=excluded.conversation_id, updated_at=datetime('now')",
                (user_email.lower(), conversation_id),
            )
            await db.commit()

    async def get_conversation_id(self, user_email: str) -> Optional[str]:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT conversation_id FROM teams_conversation_refs WHERE user_email = ?", (user_email.lower(),)
            )
            row = await cursor.fetchone()
        return row[0] if row else None
