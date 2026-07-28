"""
LLM-generated outreach messages for the chase engine (added 2026-07-22).
The single genuinely-generative piece of the chase engine's *outbound*
side (chase_parser.py already handles the *inbound*/reply-understanding
side) -- turns a deterministic template string into something that reads
like a person wrote it, personalized with the chase's actual history
(how many times we've already asked, what was promised and missed).

Deliberately built as a thin layer *on top of* the existing template, not
a replacement for it: compose_message() is told the exact substance the
template already conveys and asked to say the same thing more naturally,
never to invent new substance. That framing is what makes the safety
checks below tractable -- "does the output still say what the template
said, and nothing more" is a checkable question; "is this message good"
is not.

Guardrails (PLAN_AGENTIC_CHASE.md's "never invent a commitment" principle,
applied to generation instead of just parsing):
  - The prompt is given the template text as the required substance and
    told never to state or imply a payment date beyond what's already in
    that substance.
  - _contains_unexplained_date() rejects any output that mentions a date
    the template didn't already mention -- the one concrete, checkable
    guard against the model inventing or promising a date on the
    company's behalf.
  - Any failure (LLM error, empty output, failed validation, over length)
    falls back to the original template untouched -- compose_message()
    never raises and never blocks a send.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Dict, Optional, Tuple

from app.services.chase_evaluator import EvaluationResult, evaluate_message

logger = logging.getLogger(__name__)

_MAX_COMPOSED_LENGTH = 700  # generous for a ~60-word message; guards against a malformed/runaway completion

_MONTH_NAMES = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_DAY_RE = re.compile(
    r"\b(" + "|".join(_MONTH_NAMES.keys()) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE
)


def _extract_month_days(text: str) -> set:
    """Every (month, day) pair mentioned in text, from either ISO dates
    (2026-08-15) or natural language (August 15th, Aug 15) -- the common
    ground both a template and a composed message would use to refer to
    the *same* real date, regardless of format."""
    found = set()
    for match in _ISO_DATE_RE.finditer(text):
        _, month, day = match.groups()
        found.add((int(month), int(day)))
    for match in _MONTH_DAY_RE.finditer(text):
        month_name, day = match.groups()
        found.add((_MONTH_NAMES[month_name.lower()], int(day)))
    return found


def _contains_unexplained_date(composed: str, template: str) -> bool:
    """True if `composed` mentions a (month, day) that doesn't appear
    anywhere in `template` -- i.e. the model added a date the template
    never gave it. This is deliberately conservative (checks month+day
    only, ignores year) since that's the substance a recipient would
    actually read as a commitment."""
    composed_dates = _extract_month_days(composed)
    if not composed_dates:
        return False
    template_dates = _extract_month_days(template)
    return not composed_dates.issubset(template_dates)


def _build_prompt(kind: str, chase: Dict[str, Any], template_text: str) -> list:
    invoice_ref = chase.get("invoice_no") or chase.get("case_key") or "the invoice"
    missed = chase.get("missed_count") or 0
    nudges = chase.get("nudge_count") or 0
    system = (
        "You draft short, professional accounts-receivable follow-up messages. "
        "Rewrite the required message below in your own words -- warmer and more natural, "
        "not robotic -- but you must convey exactly this substance and nothing more:\n\n"
        f'"{template_text}"\n\n'
        f"Context: invoice {invoice_ref}, {missed} missed commitment(s) so far, {nudges} prior follow-up(s). "
        "Rules: never state or imply any payment date that isn't already in the required substance above. "
        "Never make commitments, discounts, extensions, or promises on the company's behalf. "
        "Never mention amounts not already in the substance. Keep it under 60 words. "
        "Output ONLY the message body text -- no subject line, no greeting salutation formatting, no signature."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": "Write the message."}]


async def compose_message(llm: Any, kind: str, chase: Dict[str, Any], template_text: str) -> Tuple[str, int]:
    """Returns (message_text, tokens_used). Always returns usable text --
    falls back to template_text (with 0 tokens charged) on any failure:
    LLM error, empty output, over-length output, or a detected
    unexplained date. `kind` (outreach/nudge/confirm/clarify/rechase/
    verify_check/ask_for_customer_email/checkback_ack) is accepted for future use
    (e.g. per-kind tone) but not currently branched on."""
    messages = _build_prompt(kind, chase, template_text)

    try:
        result = await llm.chat(messages, return_usage=True)
    except Exception:
        logger.exception("chase_composer: LLM call failed, falling back to template")
        return template_text, 0

    try:
        message, tokens = result
    except (TypeError, ValueError):
        # A test double or misbehaving client that ignored return_usage
        # and returned a bare message -- degrade gracefully rather than crash.
        message, tokens = result, 0

    content: Optional[str] = getattr(message, "content", None)
    if not content or not content.strip():
        return template_text, tokens

    composed = content.strip()

    if len(composed) > _MAX_COMPOSED_LENGTH:
        logger.warning("chase_composer: composed message too long (%d chars), falling back to template", len(composed))
        return template_text, tokens

    if _contains_unexplained_date(composed, template_text):
        logger.warning("chase_composer: composed message mentions a date not in the template, falling back")
        return template_text, tokens

    return composed, tokens


async def compose_and_evaluate(
    llm: Any, kind: str, chase: Dict[str, Any], template_text: str
) -> Tuple[str, int, Optional[EvaluationResult], bool]:
    """compose_message() + chase_evaluator.evaluate_message(), with the
    spec's §6.14 policy: "if evaluation fails, regenerate once. If it
    still fails, require human review." Returns (final_text,
    total_tokens, evaluation, requires_human_review).

    final_text is always safe to send -- it falls back to template_text
    (never evaluated; the template is trusted by construction) if both
    the first attempt and the regeneration fail evaluation. The caller
    still gets `requires_human_review=True` in that case so the send
    isn't silently swept under the rug -- the message that actually goes
    out is safe, but a human should know the AI composer struggled here."""
    composed_text, tokens = await compose_message(llm, kind, chase, template_text)
    total_tokens = tokens
    if composed_text == template_text:
        # compose_message already fell back internally (LLM error, empty
        # output, unexplained date, ...) -- nothing AI-generated to
        # evaluate, and the template itself is trusted by construction.
        return composed_text, total_tokens, None, False

    evaluation = evaluate_message(composed_text, chase)
    if evaluation.passed:
        return composed_text, total_tokens, evaluation, False

    logger.info("chase_composer: composed message failed evaluation (%s), regenerating once", evaluation.failures)
    retry_text, retry_tokens = await compose_message(llm, kind, chase, template_text)
    total_tokens += retry_tokens
    if retry_text == template_text:
        return retry_text, total_tokens, None, False

    retry_evaluation = evaluate_message(retry_text, chase)
    if retry_evaluation.passed:
        return retry_text, total_tokens, retry_evaluation, False

    logger.warning(
        "chase_composer: composed message failed evaluation again after regenerating (%s) -- "
        "falling back to template and flagging for human review", retry_evaluation.failures,
    )
    return template_text, total_tokens, retry_evaluation, True
