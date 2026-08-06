"""ReplyInterpreter Protocol + deterministic mock (P12)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Protocol

from app.outcome_agent.domain.types import ReplyType


@dataclass
class InterpretedReply:
    reply_type: str
    confidence: float
    promised_date: Optional[str] = None
    followup_date: Optional[str] = None
    blocker_type: Optional[str] = None
    blocker_description: Optional[str] = None
    sentiment: str = "neutral"
    needs_clarification: bool = False
    summary: str = ""
    # Explicit customer asks that ride alongside whatever reply_type this
    # is (e.g. a blocker reply that also says "email my colleague instead").
    mentioned_contact_email: Optional[str] = None
    mentioned_contact_name: Optional[str] = None
    special_instruction: Optional[str] = None
    # True only when the PM explicitly says to go ahead and contact the
    # customer directly (in response to a pm_awareness_check). Added
    # 2026-08-06 (user feedback): a PM saying "let me check, I'll get back
    # to you" must NOT be treated as authorization -- that's a checkback,
    # not a go-ahead, and the agent should keep waiting on the PM rather
    # than switching to customer-facing outreach.
    authorizes_customer_contact: bool = False


class ReplyInterpreter(Protocol):
    def interpret(self, text: str, *, now: Optional[datetime] = None) -> InterpretedReply: ...


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_NAME_RE = re.compile(r"\b(?:name is|this is|i am|i'm)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\b")
_TONE_RE = re.compile(
    r"\b(please (?:talk|be|use) (?:with )?(?:politeness|polite|care|careful|gentle)|"
    r"with (?:extra )?(?:care|politeness)|be (?:more )?(?:polite|careful|gentle))\b",
    re.IGNORECASE,
)
_AUTHORIZE_CUSTOMER_CONTACT_RE = re.compile(
    r"\b(go ahead and (?:reach out|contact|email)|"
    r"(?:please )?(?:reach out|contact|email) (?:the |them )?customer|"
    r"(?:reach out|contact) (?:them|the customer) directly|"
    r"yes,? (?:please )?(?:reach out|contact them|contact the customer))\b",
    re.IGNORECASE,
)


def _extract_meta(text: str) -> tuple[Optional[str], Optional[str], Optional[str], bool]:
    """Best-effort extraction for the deterministic/mock fallback path --
    the LLM interpreter does this properly via its own schema; this just
    keeps the mock path from silently dropping the same signal in tests."""
    email = None
    m = _EMAIL_RE.search(text)
    if m:
        email = m.group(0)
    name = None
    m = _NAME_RE.search(text)
    if m:
        name = m.group(1)
    instruction = None
    if _TONE_RE.search(text):
        instruction = text.strip()[:200]
    authorizes = bool(_AUTHORIZE_CUSTOMER_CONTACT_RE.search(text))
    return email, name, instruction, authorizes


class DeterministicReplyInterpreter:
    """Keyword/heuristic interpreter — default for demo (AI flesh optional)."""

    def interpret(self, text: str, *, now: Optional[datetime] = None) -> InterpretedReply:
        result = self._interpret_core(text, now=now)
        email, name, instruction, authorizes = _extract_meta(text or "")
        result.mentioned_contact_email = email
        result.mentioned_contact_name = name
        result.special_instruction = instruction
        result.authorizes_customer_contact = authorizes
        return result

    def _interpret_core(self, text: str, *, now: Optional[datetime] = None) -> InterpretedReply:
        t = (text or "").strip()
        low = t.lower()
        now = now or datetime.utcnow()

        if re.search(r"\b(unsubscribe|stop emailing|opt[- ]?out)\b", low):
            return InterpretedReply(ReplyType.UNSUBSCRIBE.value, 0.95, sentiment="negative", summary="opt-out")

        if re.search(r"\b(dispute|quantit(?:y|ies) (?:are )?wrong|incorrect (?:amount|invoice)|do not owe)\b", low):
            return InterpretedReply(ReplyType.DISPUTE.value, 0.9, sentiment="frustrated", summary="dispute")

        if re.search(r"\b(already paid|we paid|payment (?:was )?sent|remitted)\b", low):
            return InterpretedReply(ReplyType.PAID_CLAIM.value, 0.85, sentiment="assertive", summary="paid claim")

        if re.search(r"\b(sue|lawsuit|hostile|ridiculous|harass)\b", low):
            return InterpretedReply(ReplyType.HOSTILE.value, 0.8, sentiment="hostile", summary="hostile")

        date = _extract_date(low, now)
        if re.search(r"\b(will pay|pay(?:ment)? (?:on|by)|promise to pay)\b", low) and date:
            return InterpretedReply(
                ReplyType.PAYMENT_DATE.value, 0.88, promised_date=date, sentiment="cooperative", summary="promise"
            )

        # A blocker is a *named* obstacle that gates payment itself (approval,
        # PO, legal/contract review, dispute-driven hold) -- not just "we're
        # checking on something." "Let me check with my team and I'll confirm
        # a payment date" is a normal checkback, not a blocker: the customer
        # isn't saying payment is gated, just that the date isn't final yet.
        # See 2026-08-05 guidance: narrowed after the broader "checking
        # with|need to check/confirm/verify" catch-all (added for the
        # earlier paraphrased-blocker fix) started swallowing plain
        # checkback replies that happened to use the word "check".
        if re.search(
            r"\b(blocker|approval|po pending|purchase order (?:pending|required)|"
            r"budget (?:sign-?off|approval)|legal (?:review|sign-?off)|"
            r"contract (?:review|terms)|travel|holding|"
            r"waiting on (?:approval|sign-?off|finance|legal))\b"
            r"|\bbefore (?:we can |we |i can |i )?"
            r"(?:pay|paying|process(?:ing)? (?:this )?payment|make (?:this )?payment|"
            r"this can move forward)\b",
            low,
        ):
            fu = date or (now + timedelta(days=6)).date().isoformat()
            return InterpretedReply(
                ReplyType.BLOCKER.value,
                0.8,
                followup_date=fu,
                blocker_type="approval",
                blocker_description=t[:200],
                sentiment="cooperative",
                summary="blocker",
            )

        if re.search(
            r"\b(check back|follow[- ]?up|get back to you|checking with|check(?:ing)? "
            r"(?:this |that )?with|need to (?:verify|confirm|check)|"
            r"team is (?:reviewing|checking)|tuesday|next week|"
            r"give me (?:a|an|\d+) (?:day|week)s?)\b",
            low,
        ):
            fu = date or (now + timedelta(days=6)).date().isoformat()
            return InterpretedReply(
                ReplyType.CHECKBACK.value, 0.75, followup_date=fu, sentiment="cooperative", summary="checkback"
            )

        if re.search(r"\b(maybe|soon|not sure|whenever|eventually)\b", low):
            return InterpretedReply(
                ReplyType.VAGUE.value,
                0.35,
                needs_clarification=True,
                sentiment="evasive",
                summary="vague",
            )

        if date and re.search(r"\bpay\b", low):
            return InterpretedReply(ReplyType.PAYMENT_DATE.value, 0.7, promised_date=date, summary="dated pay")

        return InterpretedReply(ReplyType.UNKNOWN.value, 0.4, needs_clarification=True, summary="unknown")


_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _extract_date(low: str, now: datetime) -> Optional[str]:
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", low)
    if m:
        return m.group(1)

    # Relative references ("in 3 days", "give me a week", "in 2 weeks") --
    # added 2026-08-06 (user feedback): a reply like "follow up with me in
    # 3 days" was falling through to the generic 6-day checkback default
    # instead of registering the date the customer actually gave.
    m = re.search(r"\b(?:in|give me|within)\s+(a|an|\d+)\s+day(?:s)?\b", low)
    if m:
        n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
        return (now + timedelta(days=n)).date().isoformat()
    m = re.search(r"\b(?:in|give me|within)\s+(a|an|\d+)\s+week(?:s)?\b", low)
    if m:
        n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
        return (now + timedelta(weeks=n)).date().isoformat()
    if re.search(r"\btomorrow\b", low):
        return (now + timedelta(days=1)).date().isoformat()
    if re.search(r"\b(next week|early next week)\b", low):
        return (now + timedelta(weeks=1)).date().isoformat()

    months = {
        "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
        "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    m = re.search(
        r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(20\d{2}))?\b",
        low,
    )
    if m:
        month = months[m.group(1)[:3] if m.group(1)[:3] in months else m.group(1)]
        if m.group(1) in months:
            month = months[m.group(1)]
        day = int(m.group(2))
        year = int(m.group(3)) if m.group(3) else now.year
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None
    for name, weekday in _WEEKDAYS.items():
        if name in low:
            days = (weekday - now.weekday()) % 7
            if days == 0:
                days = 7
            return (now + timedelta(days=days)).date().isoformat()
    return None


DEFAULT_INTERPRETER = DeterministicReplyInterpreter()
