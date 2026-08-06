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
from typing import Any, Dict, List, Optional

from app.agent.loop import AgentLoop
from app.channels.teams import cards
from app.channels.teams.messenger import TeamsMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.config import get_settings
from app.guardrails.identity import resolve_role
from app.guardrails.magic_link import MagicLinkPayload, create_magic_link_token
from app.services.backend_client import BackendClient
from app.outcome_agent.loop.scheduler import advance_case_with_reply
from app.outcome_agent.store.case_store import CaseStore
from app.services.email_sender import EmailSender

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
        chase_store: Optional[CaseStore] = None,
        backend_client: Optional[BackendClient] = None,
        email_sender: Optional[EmailSender] = None,
        llm: Optional[Any] = None,
    ) -> None:
        self._loop = agent_loop
        self._messenger = messenger
        self._project_store = project_conversation_store or ProjectConversationStore()
        # CaseStore optional — when absent, agent-reply pre-check is inert.
        self._chase_store = chase_store
        self._backend = backend_client
        self._email_sender = email_sender
        self._llm = llm

    async def handle(self, activity: IncomingActivity) -> None:
        if activity.card_data is not None:
            await self._handle_card_action(activity)
        elif activity.text:
            await self._handle_text(activity)

    async def _handle_text(self, activity: IncomingActivity) -> None:
        text = activity.text or ""

        if await self._maybe_handle_chase_reply(activity, text):
            return

        write_action = self._detect_write_intent(text)
        if write_action:
            await self._redirect_to_web(activity, write_action)
            return

        role = resolve_role(activity.user_id)
        result = await self._loop.run(text, role=role)
        await self._messenger.send_text(activity.conversation_id, result.answer)

    async def _maybe_handle_chase_reply(self, activity: IncomingActivity, text: str) -> bool:
        """If this project chat has an open chase waiting on the PM, treat
        the message as a reply to it instead of a normal chat turn --
        PLAN_AGENTIC_CHASE.md §4.4. Returns True if handled (caller should
        stop), False to fall through to normal write-intent/chat handling.

        Deliberately simple, not NLU, for the "is this even about the
        chase" gate: a message ending in "?" is treated as a genuine
        question and left for the normal agent loop (same "simple keyword
        detection, not NLU" spirit as _detect_write_intent above) --
        chase_machine's own parser is what actually interprets anything
        that *is* routed here, so this gate only needs to be cheap, not
        exhaustive.
        """
        if self._chase_store is None:
            return False
        if text.strip().endswith("?"):
            return False

        project_number = await self._project_store.get_project_number(activity.conversation_id)
        if not project_number:
            return False

        candidates = await self._chase_store.list_open_for_project(project_number)
        if not candidates:
            return False
        # Prefer cases waiting on a reply
        waiting = [
            c
            for c in candidates
            if c.get("state") in ("waiting_for_customer", "customer_responded", "blocked", "follow_up_scheduled")
        ] or candidates

        chase = self._pick_chase_for_reply(waiting, text)
        if chase is None:
            refs = ", ".join(c.get("invoice_no") or c.get("case_key") or c["id"] for c in waiting)
            await self._messenger.send_text(
                activity.conversation_id,
                f"I have a few open invoices waiting on a reply here: {refs}. "
                f"Which one is this about? (Include the invoice number in your reply.)",
            )
            return True

        settings = get_settings()
        await advance_case_with_reply(chase["id"], text, settings, db_path=self._chase_store.db_path)
        return True

    @staticmethod
    def _pick_chase_for_reply(candidates: List[Dict[str, Any]], text: str) -> Optional[Dict[str, Any]]:
        if len(candidates) == 1:
            return candidates[0]
        lowered = text.lower()
        matches = [c for c in candidates if (c.get("invoice_no") or "").lower() in lowered and c.get("invoice_no")]
        return matches[0] if len(matches) == 1 else None

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
