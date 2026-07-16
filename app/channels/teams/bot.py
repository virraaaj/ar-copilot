"""
Teams bot core (PLAN.md §5 Phase 4): one conversation, two entry points.

  - Plain text -> routed through the *existing, unmodified* AgentLoop --
    same tools, same guardrails, same invoice-ID-free resolution as the web
    Chat page. Nothing Teams-specific about the reasoning.
  - Adaptive Card Action.Submit data -> opens a follow-up form card, or
    executes a write tool (snooze_invoice/add_comment) and replies with a
    confirmation or error card.

`IncomingActivity` is a small internal shape, not a Bot Framework SDK
`Activity` -- translating a real Activity into this at the HTTP edge is the
only place botbuilder-core (or the REST API directly) needs to appear, and
that edge doesn't exist yet (blocked on credentials, see messenger.py). This
keeps everything below fully testable without that dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.agent.loop import AgentLoop
from app.agent.tools_write import add_comment, snooze_invoice
from app.channels.teams import cards
from app.channels.teams.messenger import TeamsMessenger
from app.guardrails.identity import resolve_role
from app.guardrails.policy import PolicyViolation
from app.services.backend_client import BackendClient


@dataclass
class IncomingActivity:
    conversation_id: str
    user_id: str  # AAD object id or email -- used for identity -> role resolution
    user_name: str
    text: Optional[str] = None  # set for a plain-text message
    card_data: Optional[Dict[str, Any]] = None  # set for an Action.Submit


class TeamsBot:
    def __init__(self, agent_loop: AgentLoop, messenger: TeamsMessenger, backend: BackendClient) -> None:
        self._loop = agent_loop
        self._messenger = messenger
        self._backend = backend

    async def handle(self, activity: IncomingActivity) -> None:
        if activity.card_data is not None:
            await self._handle_card_action(activity)
        elif activity.text:
            await self._handle_text(activity)

    async def _handle_text(self, activity: IncomingActivity) -> None:
        role = resolve_role(activity.user_id)
        result = await self._loop.run(activity.text or "", role=role)
        await self._messenger.send_text(activity.conversation_id, result.answer)

    async def _handle_card_action(self, activity: IncomingActivity) -> None:
        data = activity.card_data or {}
        action = data.get("action")
        invoice_id = data.get("invoice_id")
        label = data.get("label", "this invoice")

        if action == "open_snooze_form":
            await self._messenger.send_card(activity.conversation_id, cards.snooze_form_card(invoice_id, label))
        elif action == "open_comment_form":
            await self._messenger.send_card(activity.conversation_id, cards.comment_form_card(invoice_id, label))
        elif action == "snooze_submit":
            await self._do_snooze(activity, invoice_id, label, data)
        elif action == "comment_submit":
            await self._do_comment(activity, invoice_id, label, data)
        elif action == "disambiguate_select":
            await self._continue_after_disambiguation(activity, invoice_id, label)
        else:
            await self._messenger.send_card(activity.conversation_id, cards.error_card(f"Unrecognized action: {action}"))

    async def _do_snooze(self, activity: IncomingActivity, invoice_id: str, label: str, data: Dict[str, Any]) -> None:
        reason = data.get("reason", "")
        resume_date = data.get("resume_date") or None
        try:
            await snooze_invoice(self._backend, invoice_id, reason=reason, resume_date=resume_date)
        except PolicyViolation as exc:
            await self._messenger.send_card(activity.conversation_id, cards.error_card(str(exc)))
            return
        await self._messenger.send_card(activity.conversation_id, cards.snooze_confirmation_card(label, reason, resume_date))

    async def _do_comment(self, activity: IncomingActivity, invoice_id: str, label: str, data: Dict[str, Any]) -> None:
        comment = data.get("comment", "")
        try:
            await add_comment(self._backend, invoice_id, comment=comment, source_channel="teams")
        except PolicyViolation as exc:
            await self._messenger.send_card(activity.conversation_id, cards.error_card(str(exc)))
            return
        await self._messenger.send_card(activity.conversation_id, cards.comment_confirmation_card(label, comment))

    async def _continue_after_disambiguation(self, activity: IncomingActivity, invoice_id: str, label: str) -> None:
        """Tapping a disambiguation option resolves the invoice, then hands
        back to the same reasoning loop with it pinned in history -- same
        mechanism as the web UI's pinned-invoice chat handoff."""
        role = resolve_role(activity.user_id)
        history = [{"role": "system", "content": f"The user is asking about invoice_id={invoice_id} ({label})."}]
        result = await self._loop.run(f"Tell me about {label}.", role=role, history=history)
        await self._messenger.send_text(activity.conversation_id, result.answer)
