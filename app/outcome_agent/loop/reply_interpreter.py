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


class ReplyInterpreter(Protocol):
    def interpret(self, text: str, *, now: Optional[datetime] = None) -> InterpretedReply: ...


class DeterministicReplyInterpreter:
    """Keyword/heuristic interpreter — default for demo (AI flesh optional)."""

    def interpret(self, text: str, *, now: Optional[datetime] = None) -> InterpretedReply:
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

        if re.search(r"\b(blocker|approval|holding|travel|po pending|waiting on)\b", low):
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

        if re.search(r"\b(check back|follow[- ]?up|get back to you|tuesday|next week)\b", low):
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


def _extract_date(low: str, now: datetime) -> Optional[str]:
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", low)
    if m:
        return m.group(1)
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
    if "friday" in low:
        # next Friday from now
        days = (4 - now.weekday()) % 7
        if days == 0:
            days = 7
        return (now + timedelta(days=days)).date().isoformat()
    if "tuesday" in low:
        days = (1 - now.weekday()) % 7
        if days == 0:
            days = 7
        return (now + timedelta(days=days)).date().isoformat()
    return None


DEFAULT_INTERPRETER = DeterministicReplyInterpreter()
