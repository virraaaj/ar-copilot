"""Pre-send guardrail engine — wraps chase_guardrails + agent hard rules."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.services.chase_guardrails import check_blackout_date, check_message_language


@dataclass(frozen=True)
class AgentGuardrailResult:
    allowed: bool
    reason: Optional[str] = None
    policies_checked: List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.policies_checked is None:
            object.__setattr__(self, "policies_checked", [])


def check_before_send(
    case: Dict[str, Any],
    draft: str,
    *,
    now: Optional[datetime] = None,
    dry_run: bool = True,
    allowlist: Optional[List[str]] = None,
    recipient: Optional[str] = None,
) -> AgentGuardrailResult:
    checked: List[str] = []
    world = case.get("world") or {}
    state = case.get("state")

    checked.append("paid_check")
    if float(world.get("balance_due") or 0) <= 0 or world.get("status") == "paid" or state == "paid":
        return AgentGuardrailResult(False, "world shows paid — never outreach", checked)

    checked.append("opt_out_check")
    if world.get("status") == "opted_out" or world.get("consent_ok") is False or state == "suppressed":
        return AgentGuardrailResult(False, "opted-out / suppressed", checked)

    checked.append("dispute_check")
    if world.get("status") == "disputed" or state == "disputed":
        return AgentGuardrailResult(False, "disputed — collections suppressed", checked)

    checked.append("blackout")
    today = (now or datetime.utcnow()).date() if not isinstance(now, date) else now
    if isinstance(now, datetime):
        today = now.date()
    br = check_blackout_date(today)
    if not br.allowed:
        return AgentGuardrailResult(False, br.reason, checked)

    checked.append("language")
    lr = check_message_language(draft)
    if not lr.allowed:
        return AgentGuardrailResult(False, lr.reason, checked)

    checked.append("allowlist")
    if allowlist and recipient and recipient.lower() not in allowlist and not dry_run:
        return AgentGuardrailResult(False, "recipient not on allowlist", checked)

    return AgentGuardrailResult(True, None, checked)
