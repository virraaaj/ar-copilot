"""
Proactive stage-triggered reminders (PLAN.md §5 Phase 4 — pulled forward
from Phase 5 since this is the actual "sent reminders" ask).

2026-07-16: reworked from 1:1 PM DMs to **project-level group chats** — one
conversation per project, seeded with every project contact, with all of a
project's invoices pooled into that same chat, per the ideation thread (a
PM shouldn't need a separate DM per invoice). Card buttons now deep-link to
the web app via a signed magic-link token instead of opening an in-Teams
form (see guardrails/magic_link.py and channels/teams/cards.py).

One pass: check active cases, group by project, resolve/create that
project's group conversation, send a reminder card for anything newly in an
outreach stage. Dedup key = (case_id, stage) so a nudge fires once, ever,
per stage, same as before.

Group-chat creation needs a real Bot Framework connection (POST
/v3/conversations with multiple members) — blocked on credentials same as
everything else in this channel; TeamsMessenger.create_group_conversation
is the swappable seam (FakeMessenger today, BotFrameworkMessenger once
MICROSOFT_APP_ID/PASSWORD exist, no call-site changes needed here).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

import aiosqlite

from app.channels.teams import cards
from app.channels.teams.messenger import TeamsMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.config import get_settings
from app.guardrails.magic_link import MagicLinkPayload, create_magic_link_token
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


async def _resolve_project_contacts(backend: BackendClient, project_number: Optional[str]) -> List[Dict[str, Any]]:
    if not project_number:
        return []
    return await backend.list_contacts_for_project(project_number)


async def _get_or_create_project_conversation(
    project_number: str,
    contacts: List[Dict[str, Any]],
    project_name: str,
    messenger: TeamsMessenger,
    store: ProjectConversationStore,
) -> Optional[str]:
    existing = await store.get_conversation_id(project_number)
    if existing:
        return existing

    member_emails = [c["email"] for c in contacts if c.get("email")]
    if not member_emails:
        return None

    conversation_id = await messenger.create_group_conversation(topic=project_name, member_emails=member_emails)
    await store.record(project_number, conversation_id)
    return conversation_id


def _pm_email(contacts: List[Dict[str, Any]]) -> Optional[str]:
    for c in contacts:
        if c.get("contact_type") == "pm" and c.get("email"):
            return c["email"]
    return next((c["email"] for c in contacts if c.get("email")), None)


def _reminder_urls(case_id: str, pm_email: Optional[str]) -> tuple[str, str]:
    """Builds the Snooze/Add-comment magic-link URLs for one case's card.

    The token's `email` field is best-effort identity for display/audit
    only (the case's PM, or the first contact with an email) -- it does
    NOT gate who can use the link. This app's write path already runs
    through one shared service account for every channel (see web.py's
    auth docstring); a real per-contact identity check would need the
    actual Teams click event to carry the clicking user's own identity
    (Bot Framework can do this, but only once the group chat is real and
    wired up -- blocked on credentials same as the rest of this channel).
    Tracked as a known gap, not silently papered over.
    """
    s = get_settings()
    email = pm_email or "unknown@lummus.internal"
    snooze_token = create_magic_link_token(MagicLinkPayload(email=email, action="snooze", invoice_id=case_id))
    comment_token = create_magic_link_token(MagicLinkPayload(email=email, action="comment", invoice_id=case_id))
    return (
        f"{s.WEB_BASE_URL}/link?token={snooze_token}",
        f"{s.WEB_BASE_URL}/link?token={comment_token}",
    )


async def send_due_reminders(
    backend: BackendClient,
    messenger: TeamsMessenger,
    conversation_store: ProjectConversationStore,
    dedup: ReminderDedup,
) -> int:
    """Returns how many reminders were actually sent this pass."""
    cases = await backend.list_cases(case_status="active", limit=500)
    due_by_project: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for case in cases:
        stage = case.get("current_stage_code")
        if stage not in OUTREACH_STAGES:
            continue
        if await dedup.already_sent(case["id"], stage):
            continue
        project_number = case.get("project_number")
        if not project_number:
            logger.info("Case %s has no project_number -- skipping reminder", case["id"])
            continue
        due_by_project[project_number].append(case)

    sent_count = 0
    for project_number, project_cases in due_by_project.items():
        project_name = project_cases[0].get("project_name") or "Unknown project"
        contacts = await _resolve_project_contacts(backend, project_number)
        if not contacts:
            logger.info("No contacts for project %s -- skipping %d reminder(s)", project_number, len(project_cases))
            continue

        conversation_id = await _get_or_create_project_conversation(
            project_number, contacts, project_name, messenger, conversation_store
        )
        if not conversation_id:
            logger.info("Could not resolve/create a Teams conversation for project %s -- skipping", project_number)
            continue

        pm_email = _pm_email(contacts)
        for case in project_cases:
            stage = case["current_stage_code"]
            snooze_url, comment_url = _reminder_urls(case["id"], pm_email)
            card = cards.reminder_card(
                case_key=case.get("case_key", case["id"]),
                project_name=project_name,
                stage_label=stage.replace("_", " ").title(),
                amount=_format_amount(case.get("primary_invoice_open_amount")),
                aging=case.get("primary_invoice_aging_status") or "unknown",
                snooze_url=snooze_url,
                comment_url=comment_url,
                due_date=case.get("primary_invoice_due_date"),
            )
            await messenger.send_card(conversation_id, card)
            await dedup.mark_sent(case["id"], stage)
            sent_count += 1

    return sent_count
