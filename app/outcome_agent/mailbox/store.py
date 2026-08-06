"""Local mailbox — functional email without SMTP/Graph."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite

from app.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oa_mailbox (
    id TEXT PRIMARY KEY,
    case_id TEXT,
    thread_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    from_addr TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    headers_json TEXT NOT NULL DEFAULT '{}',
    in_reply_to TEXT,
    status TEXT NOT NULL DEFAULT 'sent',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oa_mailbox_case ON oa_mailbox(case_id, created_at);
CREATE INDEX IF NOT EXISTS idx_oa_mailbox_thread ON oa_mailbox(thread_id, created_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


class MailboxStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path if db_path is not None else get_settings().STATE_DB_PATH
        self._initialized = False

    @property
    def db_path(self) -> str:
        return self._db_path

    async def _ensure(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def send(
        self,
        *,
        case_id: str,
        thread_id: str,
        to_addr: str,
        from_addr: str,
        subject: str,
        body: str,
        headers: Optional[Dict[str, Any]] = None,
        in_reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure()
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        hdrs = dict(headers or {})
        hdrs.setdefault("Message-Id", f"<{msg_id}@local.mailbox>")
        row = {
            "id": msg_id,
            "case_id": case_id,
            "thread_id": thread_id,
            "direction": "outbound",
            "from_addr": from_addr,
            "to_addr": to_addr,
            "subject": subject,
            "body": body,
            "headers": hdrs,
            "in_reply_to": in_reply_to,
            "status": "sent",
            "created_at": _now_iso(),
        }
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO oa_mailbox
                   (id, case_id, thread_id, direction, from_addr, to_addr, subject, body,
                    headers_json, in_reply_to, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["id"],
                    case_id,
                    thread_id,
                    "outbound",
                    from_addr,
                    to_addr,
                    subject,
                    body,
                    json.dumps(hdrs),
                    in_reply_to,
                    "sent",
                    row["created_at"],
                ),
            )
            await db.commit()
        return row

    async def receive_reply(
        self,
        *,
        case_id: str,
        thread_id: str,
        from_addr: str,
        to_addr: str,
        subject: str,
        body: str,
        in_reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        await self._ensure()
        msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        hdrs = {"Message-Id": f"<{msg_id}@local.mailbox>"}
        if in_reply_to:
            hdrs["In-Reply-To"] = in_reply_to
        row = {
            "id": msg_id,
            "case_id": case_id,
            "thread_id": thread_id,
            "direction": "inbound",
            "from_addr": from_addr,
            "to_addr": to_addr,
            "subject": subject,
            "body": body,
            "headers": hdrs,
            "in_reply_to": in_reply_to,
            "status": "received",
            "created_at": _now_iso(),
        }
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO oa_mailbox
                   (id, case_id, thread_id, direction, from_addr, to_addr, subject, body,
                    headers_json, in_reply_to, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["id"],
                    case_id,
                    thread_id,
                    "inbound",
                    from_addr,
                    to_addr,
                    subject,
                    body,
                    json.dumps(hdrs),
                    in_reply_to,
                    "received",
                    row["created_at"],
                ),
            )
            await db.commit()
        return row

    def _row(self, r: aiosqlite.Row) -> Dict[str, Any]:
        d = dict(r)
        d["headers"] = json.loads(d.pop("headers_json") or "{}")
        return d

    async def list_for_case(self, case_id: str) -> List[Dict[str, Any]]:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM oa_mailbox WHERE case_id=? ORDER BY created_at ASC", (case_id,)
            )
            return [self._row(r) for r in await cur.fetchall()]

    async def list_all(self, direction: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if direction:
                cur = await db.execute(
                    "SELECT * FROM oa_mailbox WHERE direction=? ORDER BY created_at DESC LIMIT ?",
                    (direction, limit),
                )
            else:
                cur = await db.execute(
                    "SELECT * FROM oa_mailbox ORDER BY created_at DESC LIMIT ?", (limit,)
                )
            return [self._row(r) for r in await cur.fetchall()]

    async def get(self, message_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM oa_mailbox WHERE id=?", (message_id,))
            row = await cur.fetchone()
            return self._row(row) if row else None

    async def wipe_all(self) -> None:
        await self._ensure()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM oa_mailbox")
            await db.commit()
