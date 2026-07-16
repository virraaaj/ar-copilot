"""
Teams bot core (PLAN.md §5 Phase 4): one conversation, two entry points.

  - Plain text -> routed through the *existing, unmodified* AgentLoop --
    same tools, same guardrails, same invoice-ID-free resolution as the web
    Chat page. A message that expresses wanting to snooze or comment is
    special-cased first (see _detect_write_intent) and answered with a
    magic-link redirect card instead, per the 2026-07-16 redesign: those
    actions now happen on the web, not via in-Teams forms.
  - Adaptive Card Action.Submit data -> currently only the invoice
    disambiguation flow uses this (picking a candidate from a list of
    matches); snooze/comment no longer have in-Teams form cards to submit.

`IncomingActivity` is a small internal shape, not a Bot Framework SDK
`Activity` -- translating a real Activity into this at the HTTP edge is the
only place botbuilder-core (or the REST API directly) needs to appear, and
that edge doesn't exist yet (blocked on credentials, see messenger.py). This
keeps everything below fully testable without that dependency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.agent.loop import AgentLoop
from app.channels.teams import cards
from app.channels.teams.messenger import TeamsMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.config import get_settings
from app.guardrails.identity import resolve_role
from app.guardrails.magic_link import MagicLinkPayload, create_magic_link_token

# Deliberately simple keyword detection, not NLU -- catches the common
# phrasing ("I want to snooze this", "can you add a comment", "pause it a
# week") without needing a model round-trip just to decide whether to
# redirect. False negatives fall through to the normal AgentLoop chat path,
# which is a safe default (worst case: the agent answers instead of
# redirecting, not a wrong action).
_SNOOZE_INTENT = re.compile(r"\bsnooze|\bpause\b|\bhold off\b", re.IGNORECASE)
_COMMENT_INTENT = re.compile(r"\bcomment|\badd a note\b|\blog a note\b", re.IGNORECASE)
# "follow up"/"reach out" etc, checked before comment/snooze -- added
# 2026-07-16 for the "have the agent email the customer on a schedule"
# feature (see followup_engine.py). Deliberately checked first: "follow up
# and add a note" should route to the follow-up setup form, not comment.
_FOLLOW_UP_INTENT = re.compile(r"\bfollow[\s-]?up\b|\breach out\b|\bchase\b|\bemail the customer\b", re.IGNORECASE)


@dataclass
class IncomingActivity:
    conversation_id: str
    user_id: str  # AAD object id or email -- used for identity -> role resolution
    user_name: str
    text: Optional[str] = None  # set for a plain-text message
    card_data: Optional[Dict[str, Any]] = None  # set for an Action.Submit


class TeamsBot:
    def __init__(
        self,
        agent_loop: AgentLoop,
        messenger: TeamsMessenger,
        project_conversation_store: Optional[ProjectConversationStore] = None,
    ) -> None:
        self._loop = agent_loop
        self._messenger = messenger
        self._project_store = project_conversation_store or ProjectConversationStore()

    async def handle(self, activity: IncomingActivity) -> None:
        if activity.card_data is not None:
            await self._handle_card_action(activity)
        elif activity.text:
            await self._handle_text(activity)

    async def _handle_text(self, activity: IncomingActivity) -> None:
        text = activity.text or ""
        write_action = self._detect_write_intent(text)
        if write_action:
            await self._redirect_to_web(activity, write_action)
            return

        role = resolve_role(activity.user_id)
        result = await self._loop.run(text, role=role)
        await self._messenger.send_text(activity.conversation_id, result.answer)

    @staticmethod
    def _detect_write_intent(text: str) -> Optional[str]:
        if _FOLLOW_UP_INTENT.search(text):
            return "follow_up"
        if _SNOOZE_INTENT.search(text):
            return "snooze"
        if _COMMENT_INTENT.search(text):
            return "comment"
        return None

    async def _redirect_to_web(self, activity: IncomingActivity, action: str) -> None:
        """A project chat's members share one pool of invoices, so a bare
        "I want to snooze" doesn't name which one -- send a link to the
        web invoice picker for *this chat's* project, scoped to only the
        invoices pooled into it, rather than guessing. The user picks the
        specific invoice on the web, then lands on that invoice's
        snooze/comment form."""
        project_number = await self._project_store.get_project_number(activity.conversation_id)
        if not project_number:
            await self._messenger.send_text(
                activity.conversation_id,
                "This chat isn't linked to a project yet, so I can't open the invoice picker for it.",
            )
            return

        s = get_settings()
        token = create_magic_link_token(
            MagicLinkPayload(email=activity.user_id, action="pick_invoice", project_number=project_number, next_action=action)
        )
        url = f"{s.WEB_BASE_URL}/link?token={token}"
        verb = {"snooze": "snooze", "comment": "add a comment to", "follow_up": "set up follow-up emails for"}[action]
        await self._messenger.send_card(
            activity.conversation_id,
            cards.redirect_card(f"Sure -- pick which invoice you'd like to {verb} on the web.", url, button_title="Pick an invoice"),
        )

    async def _handle_card_action(self, activity: IncomingActivity) -> None:
        data = activity.card_data or {}
        action = data.get("action")
        invoice_id = data.get("invoice_id")
        label = data.get("label", "this invoice")

        if action == "disambiguate_select":
            await self._continue_after_disambiguation(activity, invoice_id, label)
        else:
            await self._messenger.send_card(activity.conversation_id, cards.error_card(f"Unrecognized action: {action}"))

    async def _continue_after_disambiguation(self, activity: IncomingActivity, invoice_id: str, label: str) -> None:
        """Tapping a disambiguation option resolves the invoice, then hands
        back to the same reasoning loop with it pinned in history -- same
        mechanism as the web UI's pinned-invoice chat handoff."""
        role = resolve_role(activity.user_id)
        history = [{"role": "system", "content": f"The user is asking about invoice_id={invoice_id} ({label})."}]
        result = await self._loop.run(f"Tell me about {label}.", role=role, history=history)
        await self._messenger.send_text(activity.conversation_id, result.answer)
