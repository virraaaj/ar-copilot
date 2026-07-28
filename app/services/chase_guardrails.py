"""
Policy and Guardrail Engine (long-horizon outcome agent spec §6.15) --
hard rules checked immediately before a message actually sends, the last
gate before app/services/email_sender.py. Deliberately separate from
chase_machine.py's *decision* about what to do next: a policy violation
doesn't change the decision the state machine already made, it blocks
the ACTION derived from it -- same "decide vs. execute" split the whole
engine already uses everywhere else.

Distinct from app/guardrails/policy.py, which validates raw user input
(is this a real email address, a non-negative int, ...). This module is
specifically the collections-domain business rules the spec calls out.

Some of the spec's required policies are already enforced structurally
elsewhere and are NOT duplicated here:
  - "do not email opted-out contacts" -- an unsubscribed chase moves to
    'paused' (chase_machine.py's unsubscribe branch), which
    process_due_chases/the mail poller never dispatch a Decision for --
    there's no action to guardrail-check because none is ever produced.
  - "do not email if paid" -- commitment_tracked/verifying_payment always
    re-check the real backend `paid` status before generating any action.
  - "do not email if disputed" -- a confident dispute escalates
    immediately and the chase leaves the normal loop for good.
  - "do not exceed maximum email frequency" -- nudge_interval_days is
    already the enforced minimum gap between touches (chase_machine.py's
    ChaseConfig), not a separate check bolted on afterward.
  - allowlist enforcement -- already in chase_engine.py's
    _allowed_by_allowlist.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

# Phrases that must never appear in an outgoing message, regardless of
# who drafted it (template or AI-composed) -- spec: "do not offer
# discounts or concessions", "do not send legal threats".
_BANNED_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"\bdiscount(?:ed)?\b", r"\bwaive[ds]?\b", r"\breduced (?:amount|balance|payment)\b",
        r"\blegal action\b", r"\bcollections agency\b", r"\bsue\b", r"\blawsuit\b",
        r"\battorney\b", r"\bcredit bureau\b",
    ]
]

# Configured blackout dates (ISO YYYY-MM-DD) -- outreach is suppressed on
# these regardless of any other rule. A plain module-level list rather
# than a settings/env value since the spec treats this as demo-
# configurable policy, not a deployment secret; the Policy Viewer (§6.18)
# reads this same list to display it.
BLACKOUT_DATES: List[str] = []

# High-dollar invoices require a human to approve before the agent's
# first automated touch -- not a hard block, an approval gate (spec:
# "require human approval for invoices above configured high-dollar
# threshold").
HIGH_DOLLAR_THRESHOLD = 100000.0


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    reason: Optional[str] = None
    requires_human_approval: bool = False


def check_blackout_date(today: date) -> GuardrailResult:
    if today.isoformat() in BLACKOUT_DATES:
        return GuardrailResult(allowed=False, reason=f"{today.isoformat()} is a configured blackout date")
    return GuardrailResult(allowed=True)


def check_high_dollar_threshold(amount: Optional[float], is_strategic: bool = False) -> GuardrailResult:
    if amount is not None and amount >= HIGH_DOLLAR_THRESHOLD:
        return GuardrailResult(
            allowed=True, requires_human_approval=True,
            reason=f"${amount:,.0f} is at/above the ${HIGH_DOLLAR_THRESHOLD:,.0f} human-approval threshold",
        )
    if is_strategic:
        return GuardrailResult(allowed=True, requires_human_approval=True, reason="strategic account")
    return GuardrailResult(allowed=True)


def check_message_language(text: str) -> GuardrailResult:
    for pattern in _BANNED_PATTERNS:
        match = pattern.search(text)
        if match:
            return GuardrailResult(allowed=False, reason=f"message contains banned language: '{match.group(0)}'")
    return GuardrailResult(allowed=True)
