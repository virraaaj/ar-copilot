"""
Proactive stage-triggered reminders (PLAN.md §5 Phase 4 — pulled forward
from Phase 5 since this is the actual "sent reminders" ask). One pass:
check active cases, send a reminder card for anything newly in an outreach
stage whose PM has a known Teams conversation. Dedup key = (case_id, stage)
so a nudge fires once, ever, per stage.

PM identity is resolved per case via the project's contacts (contact_type
"pm"), not assumed to be on the case object — CaseSummaryResponse has no
such field (verified against the backend schema, Phase 1). A case whose PM
has no email, or whose email has no recorded Teams conversation yet, is
skipped and logged, not treated as an error — both are expected steady
states, not failures (a PM who's never messaged the bot simply can't be
DM'd yet, a real Teams platform constraint).
"""
from __future__ import annotations

import logging
from typing import Optional

import aiosqlite

from app.channels.teams import cards
from app.channels.teams.conversation_store import ConversationStore
from app.channels.teams.messenger import TeamsMessenger
from app.config import get_settings
from app.services.backend_client import BackendClient

logger = logging.getLogger(__name__)

OUTREACH_STAGES = frozenset({"reminder", "first_notice", "second_notice", "escalation", "final_notice"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminder_dedup (
    case_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (case_id, stage)
)
"""


class ReminderDedup:
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

    async def already_sent(self, case_id: str, stage: str) -> bool:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("SELECT 1 FROM reminder_dedup WHERE case_id = ? AND stage = ?", (case_id, stage))
            row = await cursor.fetchone()
        return row is not None

    async def mark_sent(self, case_id: str, stage: str) -> None:
        await self._ensure_schema()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("INSERT OR IGNORE INTO reminder_dedup (case_id, stage) VALUES (?, ?)", (case_id, stage))
            await db.commit()


def _format_amount(amount) -> str:
    return "$0" if amount is None else f"${amount:,.0f}"


async def _resolve_pm_email(backend: BackendClient, project_number: Optional[str]) -> Optional[str]:
    if not project_number:
        return None
    contacts = await backend.list_contacts_for_project(project_number)
    for c in contacts:
        if c.get("contact_type") == "pm" and c.get("email"):
            return c["email"]
    return None


async def send_due_reminders(
    backend: BackendClient,
    messenger: TeamsMessenger,
    conversation_store: ConversationStore,
    dedup: ReminderDedup,
) -> int:
    """Returns how many reminders were actually sent this pass."""
    cases = await backend.list_cases(case_status="active", limit=500)
    sent_count = 0

    for case in cases:
        stage = case.get("current_stage_code")
        if stage not in OUTREACH_STAGES:
            continue
        case_id = case["id"]
        if await dedup.already_sent(case_id, stage):
            continue

        pm_email = await _resolve_pm_email(backend, case.get("project_number"))
        if not pm_email:
            logger.info("No PM contact for case %s (project %s) -- skipping reminder", case_id, case.get("project_number"))
            continue

        conversation_id = await conversation_store.get_conversation_id(pm_email)
        if not conversation_id:
            logger.info("PM %s has no known Teams conversation -- skipping reminder for case %s", pm_email, case_id)
            continue

        card = cards.reminder_card(
            invoice_id=case_id,
            case_key=case.get("case_key", case_id),
            project_name=case.get("project_name") or "Unknown project",
            stage_label=stage.replace("_", " ").title(),
            amount=_format_amount(case.get("primary_invoice_open_amount")),
            aging=case.get("primary_invoice_aging_status") or "unknown",
            due_date=case.get("primary_invoice_due_date"),
        )
        await messenger.send_card(conversation_id, card)
        await dedup.mark_sent(case_id, stage)
        sent_count += 1

    return sent_count
