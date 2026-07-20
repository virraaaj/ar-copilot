"""
Manual follow-up campaign engine (added 2026-07-16): the two poller
functions behind the "follow up with the customer" feature, mirroring the
pattern already used for proactive reminders (channels/teams/proactive.py).

  - send_due_followups: sends the next email for every campaign whose
    next_send_at has arrived, via the EmailSender interface, stamped with
    V2-compatible lineage (email_lineage.py) so a reply is caught by
    Lummus's real inbound webhook exactly like a reply to an automated
    dunning email. Logged to FollowUpStore's local send history, not the
    real Lummus timeline (see followup_store.py's module docstring for why).
  - mirror_new_replies_to_teams: watches the real Lummus timeline for new
    reply_received events on cases with an active campaign, and posts them
    into that project's Teams chat -- reuses the existing real inbound
    pipeline and the existing project-conversation model, no new
    infrastructure needed on this side.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.channels.teams.messenger import TeamsMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.services.backend_client import BackendClient
from app.services.chase_store import ChaseStore
from app.services.email_lineage import build_lineage_footer, build_lineage_headers
from app.services.email_sender import EmailSender
from app.services.followup_store import FollowUpStore

logger = logging.getLogger(__name__)


def _build_followup_email(case_key: str, project_name: Optional[str], amount: Optional[float]) -> tuple[str, str]:
    subject = f"Following up on {case_key}"
    amount_str = f"${amount:,.0f}" if amount else "the outstanding balance"
    body = (
        f"<p>Hello,</p>"
        f"<p>Following up on {amount_str} outstanding"
        f"{f' for {project_name}' if project_name else ''} ({case_key}). "
        f"Please let us know if you have any questions or if payment is already in progress.</p>"
        f"<p>Thank you,<br/>Lummus AR</p>"
    )
    return subject, body


async def send_due_followups(
    backend: BackendClient, email_sender: EmailSender, store: FollowUpStore, chase_store: Optional[ChaseStore] = None
) -> int:
    """Returns how many follow-up emails were actually sent this pass.

    chase_store is optional only for backward compatibility with existing
    call sites/tests that predate the chase engine (PLAN_AGENTIC_CHASE.md
    §8: "the chase engine supersedes this feature for chased invoices") --
    when given, a case with an open chase is skipped here so a customer
    doesn't get both a fixed-cadence email and an agentic chase message
    for the same invoice."""
    due = await store.list_due()
    sent_count = 0

    for campaign in due:
        case_id = campaign["case_id"]
        if chase_store is not None and await chase_store.get_open_for_case(case_id):
            continue
        try:
            case = await backend.get_case(case_id)
        except Exception:
            logger.warning("Could not load case %s for a due follow-up -- skipping this pass", case_id)
            continue

        case_key = case.get("case_key", case_id)
        subject, body = _build_followup_email(case_key, case.get("project_name"), case.get("primary_invoice_open_amount"))
        headers = build_lineage_headers(
            case_id, invoice_no=case.get("primary_invoice_id"), stage_code=case.get("current_stage_code")
        )
        footer = build_lineage_footer(
            case_id,
            invoice_no=case.get("primary_invoice_id"),
            project_number=case.get("project_number"),
            stage_code=case.get("current_stage_code"),
        )

        await email_sender.send(campaign["customer_email"], subject, body + footer, headers)
        await store.mark_sent(
            campaign["id"], campaign["customer_email"], campaign["cadence_days"], campaign["end_date"]
        )
        sent_count += 1

    return sent_count


async def mirror_new_replies_to_teams(
    backend: BackendClient,
    store: FollowUpStore,
    project_conversation_store: ProjectConversationStore,
    messenger: TeamsMessenger,
) -> int:
    """Returns how many replies were mirrored into a Teams chat this pass."""
    mirrored_count = 0

    for campaign in await store.list_active():
        case_id = campaign["case_id"]
        try:
            case = await backend.get_case(case_id)
        except Exception:
            logger.warning("Could not load case %s to check for new replies -- skipping this pass", case_id)
            continue

        project_number = case.get("project_number")
        if not project_number:
            continue
        conversation_id = await project_conversation_store.get_conversation_id(project_number)
        if not conversation_id:
            continue

        events = await backend.get_case_timeline(case_id, limit=25)
        last_mirrored = campaign.get("last_mirrored_at") or ""
        new_replies = [
            e for e in events if e.get("event_type") == "reply_received" and (e.get("occurred_at") or "") > last_mirrored
        ]
        if not new_replies:
            continue

        new_replies.sort(key=lambda e: e.get("occurred_at") or "")
        case_key = case.get("case_key", case_id)
        for reply in new_replies:
            text = f"New reply on {case_key}: {reply.get('event_summary') or '(no text)'}"
            await messenger.send_text(conversation_id, text)
        mirrored_count += len(new_replies)

        latest = new_replies[-1].get("occurred_at") or ""
        await store.mark_mirrored(campaign["id"], latest)

    return mirrored_count
