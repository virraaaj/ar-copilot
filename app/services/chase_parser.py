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
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

VALID_INTENTS = (
    "commitment_date",
    "handoff_to_customer",
    "claims_paid",
    "dispute",
    "no_commitment",
    "unclear",
)

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
                        "handoff_to_customer: says to contact the customer instead (may include their email). "
                        "claims_paid: says the invoice is already paid. "
                        "dispute: disputes the invoice/amount/validity. "
                        "no_commitment: acknowledges but gives no date or vague timing ('soon', 'checking'). "
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
                "customer_contact_email": {
                    "type": "string",
                    "description": "Only if an email address for the customer was given in the reply. Omit otherwise.",
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
    customer_contact_email: Optional[str] = None
    raw_text: str = ""
    tokens_used: int = 0  # added 2026-07-22, for the Chases UI's per-invoice token total


def _fallback(reply_text: str, tokens: int = 0) -> ParsedReply:
    return ParsedReply(intent="unclear", confidence="low", raw_text=reply_text, tokens_used=tokens)


def _build_messages(reply_text: str, chase_context: Dict[str, Any]) -> List[Dict[str, str]]:
    today = chase_context.get("today") or date.today().isoformat()
    invoice_ref = chase_context.get("invoice_no") or chase_context.get("case_key") or "the invoice"
    target = chase_context.get("target", "the recipient")
    system = (
        "You interpret replies to automated accounts-receivable follow-up messages. "
        f"Today's date is {today}. The reply below is from {target} about {invoice_ref}, which is overdue. "
        "Call record_reply_interpretation exactly once with your interpretation. "
        "Resolve any relative dates ('Friday', 'in two weeks', 'end of month') to an absolute YYYY-MM-DD date "
        "using today's date as the reference point. If the reply gives no specific, resolvable date, do not "
        "guess one -- use intent=no_commitment instead."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": reply_text},
    ]


async def parse_chase_reply(llm: Any, reply_text: str, chase_context: Optional[Dict[str, Any]] = None) -> ParsedReply:
    """`llm` is anything with an async .chat(messages, tools, tool_choice)
    matching AzureOpenAIService's signature (ScriptedLLM in tests). Never
    raises on a malformed model response -- falls back to unclear/low so a
    parser hiccup degrades to "ask a clarifying question", not a crash."""
    context = chase_context or {}
    messages = _build_messages(reply_text, context)

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

    customer_email = args.get("customer_contact_email")
    if customer_email:
        from app.guardrails.policy import PolicyViolation, check_valid_email

        try:
            check_valid_email(customer_email)
        except PolicyViolation:
            customer_email = None

    return ParsedReply(
        intent=intent,
        confidence=confidence,
        promised_date=promised_date if intent == "commitment_date" else None,
        customer_contact_email=customer_email,
        raw_text=reply_text,
        tokens_used=tokens,
    )
