"""
The agentic chase engine's I/O layer (PLAN_AGENTIC_CHASE.md Phase C2).
Everything that actually talks to the backend, the messenger, and the
email sender lives here; every decision about *what* to do lives in the
pure chase_machine.py. run_chase_tick() is the one function main.py's
poller calls.

Two responsibilities per tick, in order:
  1. find_and_create_new_chases -- scan for active, overdue, unpaid,
     un-paused cases with no open chase yet, and create one (state=pending).
  2. process_due_chases -- for every chase whose next_action_at has
     arrived, figure out what happened (nudge timeout? commitment due?
     payment-claim verify window elapsed?), run it through chase_machine,
     and execute the resulting actions (send a message, or escalate).

Reply-driven transitions (a PM/customer actually answering) do NOT happen
here -- those come from the inbound edges (Teams' /api/messages, the
Phase C3 email poller), which call `advance_chase_with_reply` directly
when a reply arrives, same chase_machine.on_reply() call, just triggered
by an inbound event instead of a poll tick.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.channels.teams import cards
from app.channels.teams.messenger import TeamsMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.services import chase_machine
from app.services.backend_client import BackendClient
from app.services.chase_composer import compose_message
from app.services.chase_machine import ChaseConfig, Decision, Escalate, SendMessage
from app.services.chase_parser import ParsedReply, parse_chase_reply, strip_quoted_reply
from app.services.chase_store import ChaseStore
from app.services.chase_trajectory import assess_chase_trajectory
from app.services.email_sender import EmailSender

logger = logging.getLogger(__name__)

_SUBJECT_TOKEN_RE = re.compile(r"\[(AR-[A-Z0-9]{6})\]")


def _config_from_settings(s: Any) -> ChaseConfig:
    return ChaseConfig(
        max_nudges=s.CHASE_MAX_NUDGES,
        max_missed_commitments=s.CHASE_MAX_MISSED_COMMITMENTS,
        max_commitment_days=s.CHASE_MAX_COMMITMENT_DAYS,
        grace_days=s.CHASE_GRACE_DAYS,
        payment_verify_days=s.CHASE_PAYMENT_VERIFY_DAYS,
        nudge_interval_days=s.CHASE_NUDGE_INTERVAL_DAYS,
        max_clarifications=s.CHASE_MAX_CLARIFICATIONS,
        max_postponements=s.CHASE_MAX_POSTPONEMENTS,
    )


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


async def _find_pm_email(backend: BackendClient, project_number: Optional[str]) -> Optional[str]:
    if not project_number:
        return None
    try:
        contacts = await backend.list_contacts_for_project(project_number)
    except Exception:
        logger.warning("Could not load contacts for project %s", project_number)
        return None
    pm = next((c for c in contacts if c.get("contact_type") == "pm" and c.get("email")), None)
    if not pm:
        pm = next((c for c in contacts if c.get("contact_type") == "general_manager" and c.get("email")), None)
    return pm.get("email") if pm else None


async def find_and_create_new_chases(backend: BackendClient, chase_store: ChaseStore) -> int:
    """Returns how many new chases were created this pass."""
    try:
        cases = await backend.list_cases(case_status="active", limit=500)
    except Exception:
        logger.warning("Could not list active cases for chase creation -- skipping this pass")
        return 0

    today = date.today()
    created = 0
    for case in cases:
        case_id = case.get("id")
        if not case_id:
            continue
        due = _parse_date(case.get("primary_invoice_due_date"))
        if not due or due >= today:
            continue
        if case.get("active_pause_id"):
            continue
        if await chase_store.get_open_for_case(case_id):
            continue

        await chase_store.create(
            case_id,
            case_key=case.get("case_key"),
            invoice_no=case.get("primary_invoice_id"),
            project_number=case.get("project_number"),
        )
        created += 1

    return created


class _SendBudget:
    """Per-tick send cap -- a bug or a large cold-start backlog can't mass-
    message. Shared mutable counter across the whole tick, not per-chase."""

    def __init__(self, max_sends: int) -> None:
        self.max_sends = max_sends
        self.sent = 0

    def has_room(self) -> bool:
        return self.sent < self.max_sends

    def consume(self) -> None:
        self.sent += 1


def _resolve_target_email(action: SendMessage, chase: Dict[str, Any]) -> Optional[str]:
    if action.target == "pm":
        return chase.get("pm_email")
    if action.target == "customer":
        return chase.get("customer_email")
    # Any other project-contact role (bu_finance, general_manager, ...)
    # uses the generic contact_email slot set by the handoff_to_contact
    # decision (chase_machine.on_reply).
    return chase.get("contact_email")


def _allowed_by_allowlist(email: Optional[str], allowlist: List[str]) -> bool:
    if not allowlist:
        return True
    return bool(email) and email.lower() in allowlist


async def _execute_actions(
    decision: Decision,
    chase: Dict[str, Any],
    backend: BackendClient,
    messenger: TeamsMessenger,
    email_sender: EmailSender,
    project_conversation_store: ProjectConversationStore,
    chase_store: ChaseStore,
    settings: Any,
    budget: _SendBudget,
    llm: Any = None,
) -> None:
    for action in decision.actions:
        if isinstance(action, Escalate):
            await _handle_escalation(action, chase, backend, messenger, project_conversation_store, chase_store, settings)
            continue

        if not isinstance(action, SendMessage):
            continue

        if not budget.has_room():
            logger.info("Chase %s: per-tick send budget exhausted, deferring '%s' to next tick", chase["id"], action.kind)
            continue

        await _send_message(action, chase, backend, messenger, email_sender, project_conversation_store, chase_store, settings, llm)
        budget.consume()


async def _send_message(
    action: SendMessage,
    chase: Dict[str, Any],
    backend: BackendClient,
    messenger: TeamsMessenger,
    email_sender: EmailSender,
    project_conversation_store: ProjectConversationStore,
    chase_store: ChaseStore,
    settings: Any,
    llm: Any = None,
) -> None:
    # Compose first (dry-run included -- the whole point of dry-run is
    # previewing what would actually go out, so it should reflect the
    # AI-composed text too, not just the deterministic template).
    text = action.text
    composed = False
    if getattr(settings, "CHASE_COMPOSER_ENABLED", False) and llm is not None:
        composed_text, tokens = await compose_message(llm, action.kind, chase, action.text)
        await chase_store.increment_tokens(chase["id"], tokens)
        if composed_text != action.text:
            composed = True
        text = composed_text

    channel = "none"
    error: Optional[str] = None

    # Prefer Teams for the PM if this project has a known group chat --
    # replies then arrive through the already-working /api/messages edge.
    # Everything else (customer, or a PM project with no Teams chat) goes
    # by email.
    conversation_id = None
    if action.target == "pm" and chase.get("project_number"):
        conversation_id = await project_conversation_store.get_conversation_id(chase["project_number"])

    if settings.CHASE_DRY_RUN:
        channel = "dry_run"
        await chase_store.add_event(
            chase["id"], "dry_run_send",
            {"target": action.target, "kind": action.kind, "text": text, "composed": composed,
             "would_use_channel": "teams" if conversation_id else "email"},
        )
        return

    if conversation_id:
        try:
            await messenger.send_text(conversation_id, text)
            channel = "teams"
        except Exception as exc:
            logger.warning("Chase %s: Teams send failed, falling back to email: %s", chase["id"], exc)
            error = str(exc)

    if channel not in ("teams",):
        to_email = _resolve_target_email(action, chase)
        allowlist = settings.chase_to_address_allowlist
        if not to_email:
            channel = "email_failed"
            error = f"No {action.target} email address known"
        elif not _allowed_by_allowlist(to_email, allowlist):
            channel = "email_failed"
            error = f"{to_email} is not on CHASE_TO_ADDRESS_ALLOWLIST"
        else:
            subject = f"[{chase['subject_token']}] Re: invoice {chase.get('invoice_no') or chase.get('case_key')}"
            body = f"<p>{text}</p>"
            try:
                result = await email_sender.send(to_email, subject, body, [])
                channel = "email" if result.get("success") else "email_failed"
                if not result.get("success"):
                    # result.get("error") can itself be falsy/missing (a
                    # provider that reports failure with no detail) -- never
                    # let that collapse to a blank, uninformative error.
                    error = result.get("error") or "email provider reported failure with no further detail"
            except Exception as exc:
                logger.warning("Chase %s: email send failed: %s", chase["id"], exc)
                channel = "email_failed"
                # str(exc) can be empty for some exception types (e.g. a bare
                # httpx timeout with no message) -- fall back to the
                # exception's type name so the event never records a blank
                # reason for a real failure.
                error = str(exc) or f"{type(exc).__name__} (no further detail)"

    await chase_store.add_event(
        chase["id"], "outreach_sent",
        {"target": action.target, "kind": action.kind, "text": text, "channel": channel, "error": error, "composed": composed},
    )

    if channel not in ("teams", "email"):
        # Delivery genuinely failed (or was never attempted) -- the state
        # update chase_machine already decided on (e.g. "awaiting_customer")
        # was applied before this send was attempted, so left alone the
        # chase would silently wait out the full nudge interval believing
        # the customer/PM was actually contacted. Retry soon instead of
        # trusting a message that never arrived.
        retry_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
        await chase_store.update(chase["id"], next_action_at=retry_at.isoformat())

    # Mirror onto the real backend timeline (PLAN_AGENTIC_CHASE.md §4.1's
    # "every transition is visible on the invoice, not just locally" rule).
    try:
        await backend.log_response_event(
            chase["case_id"],
            raw_excerpt=f"[ar-copilot chase] -> {action.target}: {text}",
            source_channel="manual_only",
        )
    except Exception:
        logger.warning("Chase %s: could not mirror outreach to backend timeline", chase["id"])


async def _handle_escalation(
    action: Escalate,
    chase: Dict[str, Any],
    backend: BackendClient,
    messenger: TeamsMessenger,
    project_conversation_store: ProjectConversationStore,
    chase_store: ChaseStore,
    settings: Any,
) -> None:
    invoice_ref = chase.get("invoice_no") or chase.get("case_key") or chase["id"]
    if not settings.CHASE_DRY_RUN and chase.get("project_number"):
        conversation_id = await project_conversation_store.get_conversation_id(chase["project_number"])
        if conversation_id:
            review_url = f"{getattr(settings, 'WEB_BASE_URL', '')}/chases?chase_id={chase['id']}"
            card = cards.chase_escalation_card(
                invoice_ref, action.reason, chase.get("target"), chase.get("missed_count") or 0, review_url
            )
            try:
                await messenger.send_card(conversation_id, card)
            except Exception:
                logger.warning("Chase %s: could not post escalation to Teams", chase["id"])

    await chase_store.add_event(chase["id"], "escalated", {"reason": action.reason})
    try:
        await backend.log_response_event(
            chase["case_id"],
            raw_excerpt=f"[ar-copilot chase] escalated: {action.reason}",
            source_channel="manual_only",
        )
    except Exception:
        logger.warning("Chase %s: could not mirror escalation to backend timeline", chase["id"])


async def _apply_decision(chase_store: ChaseStore, chase: Dict[str, Any], decision: Decision) -> None:
    if decision.updates:
        await chase_store.update(chase["id"], **decision.updates)
    for kind, detail in decision.events:
        await chase_store.add_event(chase["id"], kind, detail)


async def _maybe_smart_escalate(
    chase: Dict[str, Any],
    llm: Any,
    chase_store: ChaseStore,
    settings: Any,
    current_count: int,
    max_count: int,
) -> Optional[Decision]:
    """Smart escalation judgment (added 2026-07-22, chase_trajectory.py):
    returns an escalate_now Decision if the AI reads this conversation as
    concerning (regardless of budget) or stalling at/past one nudge/miss
    short of the hard cap -- otherwise None, deferring to the normal
    deterministic chase_machine path. Disabled or llm-less runs always
    return None, so this is purely additive over today's behavior."""
    if not getattr(settings, "CHASE_SMART_ESCALATION_ENABLED", False) or llm is None:
        return None

    events = await chase_store.list_events(chase["id"])
    assessment, tokens = await assess_chase_trajectory(llm, chase, events)
    await chase_store.increment_tokens(chase["id"], tokens)
    await chase_store.add_event(
        chase["id"], "trajectory_assessed", {"verdict": assessment.verdict, "reason": assessment.reason}
    )

    escalate_early = assessment.verdict == "concerning" or (
        assessment.verdict == "stalling" and current_count >= max(0, max_count - 1)
    )
    if not escalate_early:
        return None

    return chase_machine.escalate_now(
        chase, reason=f"AI judged the conversation as {assessment.verdict}: {assessment.reason}"
    )


async def _process_one_due_chase(
    chase: Dict[str, Any],
    backend: BackendClient,
    messenger: TeamsMessenger,
    email_sender: EmailSender,
    chase_store: ChaseStore,
    project_conversation_store: ProjectConversationStore,
    config: ChaseConfig,
    settings: Any,
    budget: _SendBudget,
    llm: Any = None,
) -> None:
    state = chase["state"]
    decision: Optional[Decision] = None

    if state == "pending":
        pm_email = await _find_pm_email(backend, chase.get("project_number"))
        decision = chase_machine.start_pm_outreach(chase, pm_email=pm_email, config=config)

    elif state in ("awaiting_pm", "awaiting_customer", "awaiting_contact"):
        # next_action_at arrived with no reply yet (a reply would already
        # have moved the chase out of this state via advance_chase_with_reply).
        decision = await _maybe_smart_escalate(
            chase, llm, chase_store, settings, chase.get("nudge_count") or 0, config.max_nudges
        )
        if decision is None:
            decision = chase_machine.on_nudge_check(chase, config=config)

    elif state == "commitment_tracked":
        try:
            case = await backend.get_case(chase["case_id"])
            paid = case.get("case_status") == "closed_paid"
        except Exception:
            logger.warning("Chase %s: could not check payment status -- skipping this tick", chase["id"])
            return
        if not paid:
            decision = await _maybe_smart_escalate(
                chase, llm, chase_store, settings, chase.get("missed_count") or 0, config.max_missed_commitments
            )
        if decision is None:
            decision = chase_machine.on_commitment_due(chase, paid=paid, config=config)

    elif state == "verifying_payment":
        try:
            case = await backend.get_case(chase["case_id"])
            paid = case.get("case_status") == "closed_paid"
        except Exception:
            logger.warning("Chase %s: could not check payment status -- skipping this tick", chase["id"])
            return
        if not paid:
            decision = await _maybe_smart_escalate(
                chase, llm, chase_store, settings, chase.get("missed_count") or 0, config.max_missed_commitments
            )
        if decision is None:
            decision = chase_machine.on_verify_payment_timeout(chase, paid=paid, config=config)

    else:
        return

    await _apply_decision(chase_store, chase, decision)
    # Actions (e.g. resolving pm_email, switching target/customer_email on a
    # handoff) depend on fields the decision just set -- execute against the
    # merged view, not the pre-decision snapshot.
    chase = {**chase, **decision.updates}
    await _execute_actions(decision, chase, backend, messenger, email_sender, project_conversation_store, chase_store, settings, budget, llm)


async def process_due_chases(
    backend: BackendClient,
    messenger: TeamsMessenger,
    email_sender: EmailSender,
    chase_store: ChaseStore,
    project_conversation_store: ProjectConversationStore,
    settings: Any,
    llm: Any = None,
) -> int:
    """Returns how many due chases were actually processed this pass.
    `llm` (added 2026-07-22) powers the composer/smart-escalation
    features when their flags are on; omitting it (the default) just
    means those features silently stay inert -- every existing caller
    that predates them keeps working unchanged."""
    config = _config_from_settings(settings)
    budget = _SendBudget(settings.CHASE_MAX_SENDS_PER_TICK)

    due = await chase_store.list_due()
    for chase in due:
        # Also check for payment out-of-band (e.g. paid early, before any
        # commitment was even tracked) -- an active chase whose case is
        # already closed_paid should just close, regardless of state.
        try:
            case = await backend.get_case(chase["case_id"])
        except Exception:
            case = None
        if case and case.get("case_status") == "closed_paid":
            await chase_store.update(chase["id"], state="closed_paid", next_action_at=None)
            await chase_store.add_event(chase["id"], "closed", {"reason": "paid"})
            continue

        await _process_one_due_chase(
            chase, backend, messenger, email_sender, chase_store, project_conversation_store, config, settings, budget, llm
        )

    return len(due)


async def _recent_turns(chase_store: ChaseStore, chase_id: str, max_pairs: int = 3) -> List[Dict[str, str]]:
    """Reconstructs a short conversation history (assistant outreach, then
    the reply it got, oldest first) from this chase's own event log --
    added 2026-07-24 so the parser can resolve a back-reference ("the email
    I gave you earlier") instead of only ever seeing the latest reply in
    isolation. Only outreach_sent/dry_run_send carry the actual sent text
    (action_decided logs the same text a step earlier -- skipped to avoid
    duplicating each turn). Capped to the last few exchanges to keep the
    prompt bounded."""
    events = await chase_store.list_events(chase_id)
    turns: List[Dict[str, str]] = []
    for e in events:
        detail = e.get("detail") or {}
        text = detail.get("text")
        if not text:
            continue
        if e["kind"] in ("outreach_sent", "dry_run_send"):
            turns.append({"role": "assistant", "content": text})
        elif e["kind"] == "reply_received":
            turns.append({"role": "user", "content": strip_quoted_reply(text)})
    return turns[-(max_pairs * 2):]


async def advance_chase_with_reply(
    chase: Dict[str, Any],
    reply_text: str,
    llm: Any,
    backend: BackendClient,
    messenger: TeamsMessenger,
    email_sender: EmailSender,
    chase_store: ChaseStore,
    project_conversation_store: ProjectConversationStore,
    settings: Any,
) -> ParsedReply:
    """Called by the inbound edges (Teams' /api/messages, the Phase C3
    email poller) when a reply arrives for an open chase. Parses it and
    runs the resulting decision through the same execute/apply path as a
    poll tick, then returns the ParsedReply so the caller can decide
    whether to also answer normally (e.g. Teams falling through to the
    regular AgentLoop if the reply clearly wasn't about the chase)."""
    config = _config_from_settings(settings)
    budget = _SendBudget(settings.CHASE_MAX_SENDS_PER_TICK)

    recent_turns = await _recent_turns(chase_store, chase["id"])
    parsed = await parse_chase_reply(
        llm, reply_text,
        {"today": date.today().isoformat(), "invoice_no": chase.get("invoice_no"), "case_key": chase.get("case_key"),
         "target": chase.get("target")},
        recent_turns=recent_turns,
    )
    await chase_store.increment_tokens(chase["id"], parsed.tokens_used)

    await chase_store.add_event(chase["id"], "reply_received", {"target": chase.get("target"), "text": reply_text})

    resolved_contact_email = None
    if parsed.intent == "handoff_to_contact" and parsed.contact_role and chase.get("project_number"):
        try:
            contacts = await backend.list_contacts_for_project(chase["project_number"])
        except Exception:
            logger.warning("Chase %s: could not load contacts to resolve %s handoff", chase["id"], parsed.contact_role)
            contacts = []
        match = next((c for c in contacts if c.get("contact_type") == parsed.contact_role and c.get("email")), None)
        resolved_contact_email = match.get("email") if match else None

    last_question = next((t["content"] for t in reversed(recent_turns) if t["role"] == "assistant"), None)
    decision = chase_machine.on_reply(
        chase, parsed, config=config, resolved_contact_email=resolved_contact_email, last_question=last_question
    )

    await _apply_decision(chase_store, chase, decision)
    chase = {**chase, **decision.updates}
    await _execute_actions(decision, chase, backend, messenger, email_sender, project_conversation_store, chase_store, settings, budget, llm)

    return parsed


async def run_chase_tick(
    backend: BackendClient,
    messenger: TeamsMessenger,
    email_sender: EmailSender,
    chase_store: ChaseStore,
    project_conversation_store: ProjectConversationStore,
    settings: Any,
    llm: Any = None,
) -> int:
    """The single function main.py's poller calls. Returns a count purely
    for the poller's own logging (created + processed)."""
    created = await find_and_create_new_chases(backend, chase_store)
    processed = await process_due_chases(
        backend, messenger, email_sender, chase_store, project_conversation_store, settings, llm
    )
    return created + processed


def extract_subject_token(subject: str) -> Optional[str]:
    """Pulls "AR-XXXXXX" out of a subject line like "[AR-7Q2F1A] Re: invoice
    INV-1" -- see chase_store.py's _new_subject_token for the format this
    matches. Deliberately a distinct, unguessable token rather than
    matching on invoice/case numbers, so a customer quoting their own
    invoice number in an unrelated email can't accidentally match."""
    match = _SUBJECT_TOKEN_RE.search(subject or "")
    return match.group(1) if match else None


async def poll_chase_mailbox(
    mailbox_reader: Any,
    llm: Any,
    backend: BackendClient,
    messenger: TeamsMessenger,
    email_sender: EmailSender,
    chase_store: ChaseStore,
    project_conversation_store: ProjectConversationStore,
    settings: Any,
) -> int:
    """Phase C3 email inbound: reads the chase mailbox, matches replies to
    open chases by their unique subject token, and advances them the same
    way a Teams reply does. `mailbox_reader` is anything with an async
    list_recent_messages(top=int) -> list[dict] (GraphMailboxReader in
    production; see graph_mailbox.py's docstring for why this is still
    blocked on an admin consent that hasn't landed). Returns how many
    messages were newly processed this pass -- always 0, never an error,
    if mailbox_reader is None (credentials not configured yet)."""
    if mailbox_reader is None:
        return 0

    messages = await mailbox_reader.list_recent_messages(top=25)
    processed = 0
    own_address = (getattr(settings, "EMAIL_FROM_ADDRESS", "") or "").lower()

    for message in messages:
        message_id = message.get("id")
        if not message_id or await chase_store.is_mail_processed(message_id):
            continue

        # Self-addressed outreach (this UAT setup's PM/customer contacts
        # are frequently the same mailbox the chase engine sends from)
        # lands right back in this same inbox -- without this check the
        # poller would re-ingest its own outreach/nudge text as if it were
        # a genuine reply, running the chase in circles and burning the
        # clarify/nudge budget on nothing. Discovered 2026-07-23 when a
        # chase escalated after "replying to itself" twice.
        sender = ((message.get("from") or {}).get("emailAddress") or {}).get("address", "")
        if own_address and sender.lower() == own_address:
            await chase_store.mark_mail_processed(message_id)
            continue

        token = extract_subject_token(message.get("subject") or "")
        if not token:
            await chase_store.mark_mail_processed(message_id)
            continue

        chase = await chase_store.get_by_subject_token(token)
        if not chase or chase["state"] not in ("awaiting_pm", "awaiting_customer", "awaiting_contact"):
            # Unknown token, or the chase has moved on (already tracked a
            # commitment, escalated, closed) -- a late/duplicate reply to
            # an old message shouldn't reopen or double-process it.
            await chase_store.mark_mail_processed(message_id, chase["id"] if chase else None)
            continue

        body_text = message.get("bodyPreview") or ""
        await advance_chase_with_reply(
            chase, body_text, llm, backend, messenger, email_sender, chase_store, project_conversation_store, settings
        )
        await chase_store.mark_mail_processed(message_id, chase["id"])
        processed += 1

    return processed
