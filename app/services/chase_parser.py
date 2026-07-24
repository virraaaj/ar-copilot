"""
Commitment parser for the agentic chase engine (PLAN_AGENTIC_CHASE.md
§4.2, Phase C1). The one genuinely-LLM piece of the whole feature: turns
a free-text reply ("customer said end of next week", "ask bob@x.com",
"we already paid this") into a structured ParsedReply the pure state
machine (chase_machine.py) can dispatch on.

Uses a *forced* tool call (tool_choice pins the model to exactly one
function) so the output is always a parseable structure, never free text
that would need its own fragile parsing. This mirrors AgentLoop's normal
tool-calling shape but is a single-shot classification, not a multi-round
loop -- there is no tool execution here, just structured extraction.

Deliberately conservative: anything that isn't an unambiguous, absolute,
future commitment date comes back as no_commitment/unclear rather than a
guess. chase_machine.py's clarify-then-escalate path exists precisely
because this parser is expected to say "I don't know" often, not because
that's a failure -- see PLAN_AGENTIC_CHASE.md §4.2 for the full dispatch
rules this feeds.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Email/SMS clients auto-append the quoted original message (and often a
# "Sent from ..." signature) to every reply. Left in, an address sitting in
# the quote -- frequently the PM's OWN address, echoed back from a prior
# outbound message -- can get misread by the parser as "the email the
# person just gave", even when the actual new content of the reply never
# mentions an email at all. Bug found 2026-07-24: a PM said "check with
# the customer email I provided" (a back-reference, no address in this
# turn) and the parser latched onto viraj@... from the quoted "On ...
# wrote:" line instead, sending outreach to the wrong person entirely.
_QUOTE_HEADER_RE = re.compile(
    r"(?:\bOn\s+.{0,80}?\bwrote:|\bFrom:\s*.{0,80}?\bSubject:)",
    re.IGNORECASE | re.DOTALL,
)
_SIGNATURE_RE = re.compile(r"\bSent from (my|Samsung|iPhone|Galaxy)\b[^.]{0,40}", re.IGNORECASE)


def strip_quoted_reply(text: str) -> str:
    """Keeps only what was actually typed in this turn -- drops the quoted
    original message and common mobile-client signatures. Best-effort: if
    no quote marker is found, returns the text unchanged."""
    if not text:
        return text
    match = _QUOTE_HEADER_RE.search(text)
    cleaned = text[: match.start()] if match else text
    cleaned = _SIGNATURE_RE.sub("", cleaned)
    return cleaned.strip()

VALID_INTENTS = (
    "commitment_date",
    "handoff_to_customer",
    "handoff_to_contact",
    "claims_paid",
    "dispute",
    "checkback_requested",
    "no_commitment",
    "unclear",
)

# Project-contact roles the chase engine can hand off to besides the PM
# and the customer (added 2026-07-23) -- matches backend_client.py's
# CONTACT_TYPES minus "pm" (redirecting to the PM doesn't apply here, the
# PM is who's usually doing the redirecting).
CONTACT_ROLES = ("bu_finance", "corp_finance", "general_manager", "legal")

_TOOL_NAME = "record_reply_interpretation"

_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": (
            "Record the structured interpretation of a reply about an overdue invoice. "
            "Always call this exactly once, even if the reply is vague or off-topic."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": list(VALID_INTENTS),
                    "description": (
                        "commitment_date: gives a specific, resolvable date payment will happen. "
                        "handoff_to_customer: says to contact the CUSTOMER instead (may include their email). "
                        "handoff_to_contact: says to ask a specific INTERNAL project-contact role instead -- "
                        "e.g. 'ask BU Finance', 'check with the GM', 'that's a legal question'. Set contact_role too. "
                        "Do not use this for a request to contact the customer -- that's handoff_to_customer. "
                        "claims_paid: says the invoice is already paid. "
                        "dispute: an actual refusal to pay as billed, or a firm contest of the invoice/amount/"
                        "validity ('we're not paying this', 'this amount is wrong', 'we're disputing this "
                        "charge'). Do NOT use dispute just because they mention checking on a possible quality/"
                        "delivery/service concern -- 'let me check with the team, I heard the service wasn't up "
                        "to par' is investigating, not refusing to pay; that's no_commitment (set confidence=low "
                        "if you're at all unsure whether it's a real dispute -- an escalation to a human should "
                        "only happen for a genuine, confident dispute, never a tentative one). "
                        "checkback_requested: they're not committing to a payment date, but are explicitly asking "
                        "you to check back with THEM later, or naming a point when they'll know more -- "
                        "'let me check on this and get back to you', 'check with me again in 5 days', 'ask me "
                        "again next week', 'I'll know more after I speak to the team'. This is about deferring "
                        "the CONVERSATION, not promising payment -- if a payment date is given, use "
                        "commitment_date instead even if they also ask you to check back. Set followup_date if "
                        "they gave a specific timeframe for when to check back; omit it if they didn't (e.g. "
                        "just 'let me check on it' with no timing at all). "
                        "no_commitment: acknowledges but gives no date, no redirect, and no check-back timing at "
                        "all -- just a bare acknowledgment ('ok', 'noted'). "
                        "unclear: doesn't meaningfully address the question."
                    ),
                },
                "promised_date": {
                    "type": "string",
                    "description": (
                        "Only for intent=commitment_date. An absolute ISO date (YYYY-MM-DD), resolved against "
                        "today's date given in the prompt -- e.g. 'Friday' or 'next week' must be converted to "
                        "a real calendar date, not left relative. Omit entirely if not commitment_date."
                    ),
                },
                "followup_date": {
                    "type": "string",
                    "description": (
                        "Only for intent=checkback_requested, and only if they gave a specific timeframe for "
                        "when to check back ('in 5 days', 'next Monday', 'end of week'). An absolute ISO date "
                        "(YYYY-MM-DD) resolved against today's date. Omit if no timeframe was given at all."
                    ),
                },
                "customer_contact_email": {
                    "type": "string",
                    "description": "Only if an email address for the customer was given in the reply. Omit otherwise.",
                },
                "contact_role": {
                    "type": "string",
                    "enum": list(CONTACT_ROLES),
                    "description": (
                        "Only for intent=handoff_to_contact. Which internal role to redirect to: "
                        "bu_finance (business-unit finance), corp_finance (corporate finance), "
                        "general_manager, or legal. Pick the closest match -- do not guess a role that "
                        "wasn't clearly meant."
                    ),
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "low"],
                    "description": (
                        "high only if the intent and (when applicable) the date are unambiguous. "
                        "low for anything requiring interpretation or guessing."
                    ),
                },
            },
            "required": ["intent", "confidence"],
        },
    },
}


@dataclass(frozen=True)
class ParsedReply:
    intent: str
    confidence: str  # 'high' | 'low'
    promised_date: Optional[str] = None
    followup_date: Optional[str] = None  # only for intent=checkback_requested, added 2026-07-24
    customer_contact_email: Optional[str] = None
    contact_role: Optional[str] = None  # only for intent=handoff_to_contact, added 2026-07-23
    raw_text: str = ""
    tokens_used: int = 0  # added 2026-07-22, for the Chases UI's per-invoice token total


def _fallback(reply_text: str, tokens: int = 0) -> ParsedReply:
    return ParsedReply(intent="unclear", confidence="low", raw_text=reply_text, tokens_used=tokens)


def _build_messages(
    reply_text: str, chase_context: Dict[str, Any], recent_turns: Optional[List[Dict[str, str]]] = None
) -> List[Dict[str, str]]:
    today = chase_context.get("today") or date.today().isoformat()
    invoice_ref = chase_context.get("invoice_no") or chase_context.get("case_key") or "the invoice"
    target = chase_context.get("target", "the recipient")
    system = (
        "You interpret replies to automated accounts-receivable follow-up messages. "
        f"Today's date is {today}. The reply below is from {target} about {invoice_ref}, which is overdue. "
        "Call record_reply_interpretation exactly once with your interpretation. "
        "Check in this order: (1) does it redirect to someone else -- the customer, or an internal role like "
        "BU Finance/legal/GM (handoff_to_customer / handoff_to_contact, even if no date is given -- a redirect "
        "IS the answer, not a non-answer)? (2) does it claim the invoice is already paid (claims_paid)? "
        "(3) does it firmly refuse to pay or contest the invoice (dispute -- only for a genuine, confident "
        "dispute; a mention of checking on a possible quality/service concern that hasn't been confirmed is "
        "no_commitment, not dispute -- disputes trigger an immediate human escalation, so getting this wrong "
        "pulls a human in over something that might resolve on its own)? (4) does it give a specific date "
        "(commitment_date)? (5) do they ask you to check back with them later, or name a point when they'll "
        "know more, without committing to a payment date (checkback_requested -- 'let me check on this', "
        "'check with me again in 5 days', 'ask me next week', 'I'll know more after I speak to the team')? Set "
        "followup_date only if a specific timeframe was actually given. "
        "Only if none of those apply -- a bare acknowledgment with no date, no redirect, and no check-back "
        "timing at all ('ok', 'noted') -- use no_commitment. Example: 'reach out to bob@x.com' or 'ask BU "
        "Finance' is a handoff, NOT no_commitment, even though it names no payment date. "
        "Resolve any relative dates ('Friday', 'in two weeks', 'end of month') to an absolute YYYY-MM-DD date "
        "using today's date as the reference point. Do not guess a date that wasn't given. "
        "Any messages below before the final one are this same conversation's recent history (your own prior "
        "questions and their earlier replies) -- use them to resolve a back-reference like 'the email I gave "
        "you earlier' or 'like I said'. Only extract customer_contact_email/contact_role from what the person "
        "actually said (in this turn or an earlier one) -- never from an address that only appears because it "
        "was quoted back from their own prior message."
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(recent_turns or [])
    messages.append({"role": "user", "content": reply_text})
    return messages


async def parse_chase_reply(
    llm: Any,
    reply_text: str,
    chase_context: Optional[Dict[str, Any]] = None,
    recent_turns: Optional[List[Dict[str, str]]] = None,
) -> ParsedReply:
    """`llm` is anything with an async .chat(messages, tools, tool_choice)
    matching AzureOpenAIService's signature (ScriptedLLM in tests). Never
    raises on a malformed model response -- falls back to unclear/low so a
    parser hiccup degrades to "ask a clarifying question", not a crash.

    `recent_turns` (added 2026-07-24): optional [{"role", "content"}, ...]
    of this chase's recent outreach/reply history, oldest first -- lets the
    model resolve a back-reference to an earlier turn instead of only ever
    seeing the latest reply in isolation. `reply_text` itself is cleaned of
    quoted-reply content (see strip_quoted_reply) before going to the model,
    but ParsedReply.raw_text keeps the original for the audit trail."""
    context = chase_context or {}
    cleaned_reply = strip_quoted_reply(reply_text)
    messages = _build_messages(cleaned_reply, context, recent_turns)

    try:
        result = await llm.chat(
            messages,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
            return_usage=True,
        )
    except Exception:
        logger.exception("chase_parser: LLM call failed, falling back to unclear")
        return _fallback(reply_text)

    try:
        message, tokens = result
    except (TypeError, ValueError):
        # A test double or misbehaving client that ignored return_usage
        # and returned a bare message -- degrade gracefully rather than crash.
        message, tokens = result, 0

    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        logger.warning("chase_parser: model did not call %s despite forced tool_choice", _TOOL_NAME)
        return _fallback(reply_text, tokens)

    try:
        args = json.loads(tool_calls[0].function.arguments or "{}")
    except (json.JSONDecodeError, AttributeError):
        logger.warning("chase_parser: could not parse tool call arguments")
        return _fallback(reply_text, tokens)

    intent = args.get("intent")
    if intent not in VALID_INTENTS:
        return _fallback(reply_text, tokens)

    confidence = args.get("confidence") if args.get("confidence") in ("high", "low") else "low"

    promised_date = args.get("promised_date")
    if intent == "commitment_date" and promised_date:
        try:
            date.fromisoformat(promised_date)
        except (ValueError, TypeError):
            # Model returned something that isn't a real ISO date -- don't
            # let a malformed date string masquerade as a commitment.
            return ParsedReply(intent="unclear", confidence="low", raw_text=reply_text, tokens_used=tokens)
    elif intent == "commitment_date" and not promised_date:
        # Claimed a date commitment but didn't actually give one -- treat
        # as unclear rather than tracking a commitment with no date.
        return ParsedReply(intent="unclear", confidence="low", raw_text=reply_text, tokens_used=tokens)

    followup_date = args.get("followup_date")
    if intent == "checkback_requested" and followup_date:
        try:
            date.fromisoformat(followup_date)
        except (ValueError, TypeError):
            # Malformed date -- fall back to no timeframe rather than
            # scheduling a follow-up against garbage.
            followup_date = None

    customer_email = args.get("customer_contact_email")
    if customer_email:
        from app.guardrails.policy import PolicyViolation, check_valid_email

        try:
            check_valid_email(customer_email)
        except PolicyViolation:
            customer_email = None

    contact_role = args.get("contact_role")
    if intent == "handoff_to_contact" and contact_role not in CONTACT_ROLES:
        # Claimed a handoff but didn't name a role we recognize -- don't
        # let an invented/garbled role masquerade as a real routing target.
        return ParsedReply(intent="unclear", confidence="low", raw_text=reply_text, tokens_used=tokens)

    return ParsedReply(
        intent=intent,
        confidence=confidence,
        promised_date=promised_date if intent == "commitment_date" else None,
        followup_date=followup_date if intent == "checkback_requested" else None,
        customer_contact_email=customer_email,
        contact_role=contact_role if intent == "handoff_to_contact" else None,
        raw_text=reply_text,
        tokens_used=tokens,
    )
