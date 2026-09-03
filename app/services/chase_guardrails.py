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

        # Rule A -- the agent has no authority to change the terms of the
        # debt (or imply it could): extensions, "more time", payment
        # plans/instalments, revised due dates, or altered credit terms.
        # Scoped to "due date" (not bare "date") so questions like "is
        # there a new date we should track?" -- which ask about a payment
        # date, not the invoice's due date -- stay allowed.
        #
        # "extend"/"extension" are scoped to the offer-of-more-time sense
        # (an offering verb next to it, or an extension applied to a
        # deadline/invoice/payment) rather than the bare word, because the
        # bare word also appears in unrelated legitimate copy ("extended
        # project scope", "extended warranty", "the extended team") --
        # and check_before_send (loop/guardrails.py:62) hard-blocks the
        # send with no fallback on a match, so an over-broad pattern here
        # silently drops a real chase message (see executor.py:146-158).
        r"\b(?:offer|grant|give|giving|provide|arrange|allow)\w*\s+(?:you\s+)?(?:an?\s+)?extension\b",
        r"\bextension\s+(?:on|to|of)\s+(?:the\s+)?(?:invoice|due date|deadline|payment|balance)\b",
        r"\bextend\w*\s+(?:the\s+|your\s+)?(?:due date|deadline|payment|terms|invoice)\b",
        r"\bwould an extension\b",
        r"\bmore time\b",
        r"\bpayment plan(?:s)?\b", r"\binstal(?:l)?ment(?:s)?\b",
        r"\b(?:revised|updated|new|pushed[- ]back) due date\b",
        r"\bpush(?:ed|ing)? back the due date\b",
        r"\bcredit terms\b",
        # Scoped to the act of changing terms (a change/adjust/modify verb
        # right before "terms"), not a factual mention of terms already in
        # place -- "the payment terms are net-30" must stay allowed.
        r"\b(?:revise|revising|change|changing|adjust|adjusting|modify|modifying) (?:the )?terms\b",

        # Rule A -- open invitations to negotiate the debt.
        r"\blet us know what works\b", r"\bwork something out\b",
        r"\bhappy to discuss options\b", r"\bdiscuss (?:payment )?options\b",

        # Rule B -- the agent must never state or imply a consequence of
        # non-payment, including "or else" framing around service itself
        # (not just legal/credit consequences).
        r"\bsuspend(?:ing)? (?:the )?(?:service|work|deliveries)\b",
        r"\bwithhold(?:ing)? (?:the )?(?:service|work|deliveries)\b",
        r"\bstop(?:ping)? (?:the )?deliveries\b",
        r"\b(?:place|placing|put|putting) (?:the |your )?account on hold\b",
        r"\baccount (?:will be |is |remains )?on hold\b",

        # Rule C -- added 2026-09-03: partial payment is not accepted on an
        # overdue invoice, full stop. Red-teaming found this leaking through
        # the existing patterns even though "payment plan(s)"/"instalment(s)"
        # were already covered, because part-payment can be phrased without
        # either of those words ("pay half now", "any amount you can",
        # "smaller amounts" ...). Each pattern below is scoped to the actual
        # partial-payment phrasing, not bare words like "amount" or "half"
        # on their own, so factual mentions of the invoice amount ("the
        # invoice amount is $45,000", "confirm the payment amount you have
        # processed") stay allowed -- see the false-positive note on the
        # extension patterns above for why that scoping matters here too.
        r"\bpart(?:ial)? payment(?:s)?\b",
        r"\bpay(?:ing)?\s+half\b", r"\bhalf\s+now\b",
        r"\bsplit\w*\s+(?:the\s+|this\s+)?(?:balance|invoice|payment|amount)\b",
        r"\bsplit\w*\s+(?:this|it)\s+into\b",
        r"\bbreak\w*\s+(?:this|it|the\s+balance|the\s+invoice|the\s+payment)\s+into\b",
        r"\bsmaller amounts?\b",
        r"\bany amount you can\b",
        r"\bsomething on account\b",
        r"\bpay what you can\b",
    ]
]

# Configured blackout dates (ISO YYYY-MM-DD) -- outreach is suppressed on
# these regardless of any other rule. A plain module-level list rather
# than a settings/env value since the spec treats this as demo-
# configurable policy, not a deployment secret; the Policy Viewer (§6.18)
# reads this same list to display it.
BLACKOUT_DATES: List[str] = []

@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    reason: Optional[str] = None


def check_blackout_date(today: date) -> GuardrailResult:
    if today.isoformat() in BLACKOUT_DATES:
        return GuardrailResult(allowed=False, reason=f"{today.isoformat()} is a configured blackout date")
    return GuardrailResult(allowed=True)


def check_message_language(text: str) -> GuardrailResult:
    for pattern in _BANNED_PATTERNS:
        match = pattern.search(text)
        if match:
            return GuardrailResult(allowed=False, reason=f"message contains banned language: '{match.group(0)}'")
    return GuardrailResult(allowed=True)
