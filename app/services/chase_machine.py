"""
Pure state-transition logic for the agentic chase engine (PLAN_AGENTIC_
CHASE.md §4.1/§6 Phase C0). No I/O here at all -- every function takes a
chase dict (as returned by ChaseStore) plus whatever already-fetched facts
it needs, and returns a Decision: the field updates to persist, the
actions to actually execute (send a message, escalate), and the events to
log. app/services/chase_engine.py is the only caller, and it's the only
place that touches the backend, the messenger, or the email sender --
that split is what makes every budget/escalation rule here exhaustively
unit-testable without mocking a single HTTP call.

The two invariants from the plan, enforced here and nowhere else:
  - every cycle consumes budget (nudge_count / missed_count are capped;
    hitting the cap always escalates, never loops silently)
  - "paid" is decided by the caller (a real backend check) and handed in
    as a plain bool -- this module never treats a reply as proof of payment
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services.chase_parser import ParsedReply


@dataclass(frozen=True)
class ChaseConfig:
    max_nudges: int = 3
    max_missed_commitments: int = 3
    max_commitment_days: int = 90
    grace_days: int = 2
    payment_verify_days: int = 3
    nudge_interval_days: int = 3
    max_clarifications: int = 1


@dataclass(frozen=True)
class SendMessage:
    target: str  # 'pm' | 'customer'
    kind: str  # 'outreach' | 'nudge' | 'confirm' | 'clarify' | 'rechase' | 'verify_check' | 'ask_for_customer_email'
    text: str


@dataclass(frozen=True)
class Escalate:
    reason: str


Action = Any  # SendMessage | Escalate


@dataclass
class Decision:
    updates: Dict[str, Any] = field(default_factory=dict)
    actions: List[Action] = field(default_factory=list)
    events: List[tuple] = field(default_factory=list)  # (kind, detail_dict)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def escalate_now(chase: Dict[str, Any], reason: str) -> Decision:
    """Immediate escalation, bypassing the nudge/miss counters entirely --
    added 2026-07-22 for smart escalation judgment (chase_trajectory.py).
    chase_engine.py calls this instead of on_nudge_check/on_commitment_due
    when the AI trajectory assessment says the conversation is genuinely
    concerning (hostile, evasive, disputing) or has stalled well before
    the deterministic budget would have caught it -- this only ever fires
    *earlier* than the hard caps would, never grants more patience than
    them, so a chase's worst case is unchanged even if this is wrong."""
    return Decision(
        updates={"state": "escalated", "next_action_at": None},
        actions=[Escalate(reason=reason)],
        events=[("escalated", {"reason": reason})],
    )


def start_pm_outreach(chase: Dict[str, Any], pm_email: Optional[str], config: ChaseConfig = ChaseConfig()) -> Decision:
    """A brand-new chase (state='pending') always starts by asking the PM
    -- per the boss's own sample flow, the PM is always the first touch."""
    text = (
        f"Invoice {chase.get('invoice_no') or chase.get('case_key')} is now overdue. "
        f"Is there a payment date I should be tracking, or should I check with the customer directly?"
    )
    return Decision(
        updates={
            "state": "awaiting_pm",
            "target": "pm",
            "pm_email": pm_email,
            "nudge_count": 0,
            "next_action_at": _iso(_now() + timedelta(days=config.nudge_interval_days)),
            "last_outreach_at": _iso(_now()),
        },
        actions=[SendMessage(target="pm", kind="outreach", text=text)],
        events=[("action_decided", {"target": "pm", "kind": "outreach", "text": text})],
    )


def on_nudge_check(chase: Dict[str, Any], config: ChaseConfig = ChaseConfig()) -> Decision:
    """Called when a chase in awaiting_pm/awaiting_customer's next_action_at
    has arrived with no reply received. Nudge again, or escalate once the
    budget for this target is exhausted."""
    target = chase["target"]
    nudge_count = chase.get("nudge_count") or 0

    if nudge_count >= config.max_nudges:
        return Decision(
            updates={"state": "escalated", "next_action_at": None},
            actions=[Escalate(reason=f"No reply from {target} after {nudge_count} nudges.")],
            events=[("escalated", {"reason": "nudge_budget_exhausted", "target": target, "nudge_count": nudge_count})],
        )

    who = "the customer" if target == "customer" else "you"
    text = f"Following up -- still hoping to hear back about invoice {chase.get('invoice_no') or chase.get('case_key')}."
    return Decision(
        updates={
            "nudge_count": nudge_count + 1,
            "next_action_at": _iso(_now() + timedelta(days=config.nudge_interval_days)),
            "last_outreach_at": _iso(_now()),
        },
        actions=[SendMessage(target=target, kind="nudge", text=text)],
        events=[("action_decided", {"target": target, "kind": "nudge", "text": text})],
    )


def on_reply(
    chase: Dict[str, Any],
    parsed: ParsedReply,
    config: ChaseConfig = ChaseConfig(),
) -> Decision:
    """The engine has already fetched the reply, run it through
    chase_parser.parse_chase_reply, and knows which target (pm/customer)
    it came from. This function decides what happens next."""
    target = chase["target"]
    invoice_ref = chase.get("invoice_no") or chase.get("case_key")

    if parsed.intent == "dispute":
        return Decision(
            updates={"state": "escalated", "next_action_at": None},
            actions=[Escalate(reason=f"{target} disputed invoice {invoice_ref}.")],
            events=[("escalated", {"reason": "dispute", "target": target})],
        )

    if parsed.intent == "claims_paid":
        return Decision(
            updates={
                "state": "verifying_payment",
                "next_action_at": _iso(_now() + timedelta(days=config.payment_verify_days)),
            },
            actions=[SendMessage(target=target, kind="verify_check",
                                  text="Thanks -- I'll confirm on our end and follow up if it's not showing yet.")],
            events=[("reply_parsed", {"intent": "claims_paid", "target": target})],
        )

    if parsed.intent == "commitment_date" and parsed.confidence == "high" and parsed.promised_date:
        promised = datetime.fromisoformat(parsed.promised_date)
        if promised.date() < _now().date():
            return _clarify_or_escalate(chase, config, reason="promised date was in the past")
        if (promised.date() - _now().date()).days > config.max_commitment_days:
            return Decision(
                updates={"state": "escalated", "next_action_at": None},
                actions=[Escalate(reason=f"{target} promised a date more than {config.max_commitment_days} days out.")],
                events=[("escalated", {"reason": "commitment_too_far_out", "promised_date": parsed.promised_date})],
            )
        return Decision(
            updates={
                "state": "commitment_tracked",
                "promised_date": parsed.promised_date,
                "promised_by": target,
                "nudge_count": 0,
                "clarify_count": 0,
                "next_action_at": _iso(promised + timedelta(days=config.grace_days)),
            },
            actions=[SendMessage(
                target=target, kind="confirm",
                text=f"Got it -- expecting payment by {parsed.promised_date}. I'll check back then.",
            )],
            events=[("commitment_tracked", {"promised_date": parsed.promised_date, "promised_by": target})],
        )

    if parsed.intent == "handoff_to_customer" and parsed.confidence == "high":
        if target == "customer":
            # Customer can't hand off to themselves -- treat as unclear.
            return _clarify_or_escalate(chase, config, reason="handoff from the customer side is not meaningful")
        if parsed.customer_contact_email:
            text = (
                f"Hello -- following up on invoice {invoice_ref}, which is now overdue. "
                f"Is there a payment date you can share?"
            )
            return Decision(
                updates={
                    "state": "awaiting_customer",
                    "target": "customer",
                    "customer_email": parsed.customer_contact_email,
                    "nudge_count": 0,
                    "clarify_count": 0,
                    "next_action_at": _iso(_now() + timedelta(days=config.nudge_interval_days)),
                    "last_outreach_at": _iso(_now()),
                },
                actions=[SendMessage(target="customer", kind="outreach", text=text)],
                events=[("handoff_to_customer", {"customer_email": parsed.customer_contact_email})],
            )
        if chase.get("customer_email"):
            # Already have an address on file (e.g. from a prior follow-up
            # campaign) -- use it even though this reply didn't repeat it.
            text = (
                f"Hello -- following up on invoice {invoice_ref}, which is now overdue. "
                f"Is there a payment date you can share?"
            )
            return Decision(
                updates={
                    "state": "awaiting_customer",
                    "target": "customer",
                    "nudge_count": 0,
                    "clarify_count": 0,
                    "next_action_at": _iso(_now() + timedelta(days=config.nudge_interval_days)),
                    "last_outreach_at": _iso(_now()),
                },
                actions=[SendMessage(target="customer", kind="outreach", text=text)],
                events=[("handoff_to_customer", {"customer_email": chase.get("customer_email")})],
            )
        # No customer address known anywhere -- ask the PM for it. Stays
        # targeted at the PM; this is itself a chase message, not a normal
        # nudge, so it uses the clarify budget rather than the nudge one.
        return Decision(
            updates={"clarify_count": (chase.get("clarify_count") or 0) + 1,
                      "next_action_at": _iso(_now() + timedelta(days=config.nudge_interval_days))},
            actions=[SendMessage(target="pm", kind="ask_for_customer_email",
                                  text="Could you share the customer's email so I can follow up with them directly?")],
            events=[("clarify_requested", {"reason": "missing_customer_email"})],
        )

    # no_commitment / unclear / low confidence on anything else
    return _clarify_or_escalate(chase, config, reason=f"reply did not resolve to a clear commitment (intent={parsed.intent})")


def _clarify_or_escalate(chase: Dict[str, Any], config: ChaseConfig, reason: str) -> Decision:
    target = chase["target"]
    clarify_count = chase.get("clarify_count") or 0
    if clarify_count >= config.max_clarifications:
        return Decision(
            updates={"state": "escalated", "next_action_at": None},
            actions=[Escalate(reason=f"Could not get a clear commitment from {target}: {reason}.")],
            events=[("escalated", {"reason": "clarify_budget_exhausted", "detail": reason})],
        )
    text = "Thanks for the update -- is there a specific date I should expect payment by?"
    return Decision(
        updates={
            "clarify_count": clarify_count + 1,
            "next_action_at": _iso(_now() + timedelta(days=config.nudge_interval_days)),
            "last_outreach_at": _iso(_now()),
        },
        actions=[SendMessage(target=target, kind="clarify", text=text)],
        events=[("clarify_requested", {"reason": reason})],
    )


def on_commitment_due(chase: Dict[str, Any], paid: bool, config: ChaseConfig = ChaseConfig()) -> Decision:
    """Called once a tracked commitment's promised_date + grace_days has
    arrived. `paid` must come from a real backend check, never from a
    message."""
    if paid:
        return Decision(
            updates={"state": "closed_paid", "next_action_at": None},
            actions=[],
            events=[("closed", {"reason": "paid"})],
        )

    missed_count = (chase.get("missed_count") or 0) + 1
    target = chase["promised_by"] or chase["target"]
    invoice_ref = chase.get("invoice_no") or chase.get("case_key")

    if missed_count >= config.max_missed_commitments:
        return Decision(
            updates={"state": "escalated", "missed_count": missed_count, "next_action_at": None},
            actions=[Escalate(reason=f"Missed {missed_count} commitment(s) on invoice {invoice_ref}.")],
            events=[("escalated", {"reason": "missed_commitment_budget_exhausted", "missed_count": missed_count})],
        )

    text = (
        f"The payment date you gave for invoice {invoice_ref} ({chase.get('promised_date')}) has passed "
        f"and it's still showing as unpaid. Can you give me an update?"
    )
    return Decision(
        updates={
            "state": f"awaiting_{target}",
            "target": target,
            "missed_count": missed_count,
            "nudge_count": 0,
            "clarify_count": 0,
            "promised_date": None,
            "promised_by": None,
            "next_action_at": _iso(_now() + timedelta(days=config.nudge_interval_days)),
            "last_outreach_at": _iso(_now()),
        },
        actions=[SendMessage(target=target, kind="rechase", text=text)],
        events=[("commitment_missed", {"missed_count": missed_count, "target": target})],
    )


def on_verify_payment_timeout(chase: Dict[str, Any], paid: bool, config: ChaseConfig = ChaseConfig()) -> Decision:
    """Called once a 'claims_paid' verification window elapses. Same idea
    as on_commitment_due but for the verifying_payment state, which has no
    promised_date of its own to check against."""
    if paid:
        return Decision(
            updates={"state": "closed_paid", "next_action_at": None},
            actions=[],
            events=[("closed", {"reason": "paid"})],
        )

    missed_count = (chase.get("missed_count") or 0) + 1
    target = chase["target"]
    invoice_ref = chase.get("invoice_no") or chase.get("case_key")

    if missed_count >= config.max_missed_commitments:
        return Decision(
            updates={"state": "escalated", "missed_count": missed_count, "next_action_at": None},
            actions=[Escalate(reason=f"Invoice {invoice_ref} still unpaid after a claimed payment could not be verified.")],
            events=[("escalated", {"reason": "payment_claim_unverified", "missed_count": missed_count})],
        )

    text = (
        f"I'm still not seeing payment for invoice {invoice_ref} on our end -- "
        f"could you double check and let me know?"
    )
    return Decision(
        updates={
            "state": f"awaiting_{target}",
            "missed_count": missed_count,
            "nudge_count": 0,
            "next_action_at": _iso(_now() + timedelta(days=config.nudge_interval_days)),
            "last_outreach_at": _iso(_now()),
        },
        actions=[SendMessage(target=target, kind="verify_check", text=text)],
        events=[("commitment_missed", {"missed_count": missed_count, "reason": "unverified_payment_claim"})],
    )
