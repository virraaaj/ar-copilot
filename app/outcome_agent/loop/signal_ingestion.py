"""Signal ingestion — replies, payments, disputes, date advances."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from app.outcome_agent.domain.dialogue_model import DialogueSnapshot
from app.outcome_agent.domain.state_machine import transition
from app.outcome_agent.domain.types import AutonomyBudget
from app.outcome_agent.domain.world_model import WorldSnapshot
from app.outcome_agent.loop.reply_interpreter import DEFAULT_INTERPRETER, InterpretedReply
from app.outcome_agent.memory.temporal_graph import supersede_blocker_with_commitment
from app.outcome_agent.domain.budgets import consume_postponement, reset_unanswered_on_reply


async def _record_payment_date_commitment(
    case: Dict[str, Any],
    dialogue: DialogueSnapshot,
    commitments: list,
    blockers: list,
    graph_store,
    interp: InterpretedReply,
) -> None:
    """Create/supersede an active payment_date commitment for interp.promised_date.

    Factored out 2026-09-03 (Change 1) so the hostile-with-date branch in
    apply_reply_signal below can record a payment date exactly the way the
    plain payment_date branch does -- same commitment shape, same
    supersede-existing-commitments behaviour, same
    dialogue.customer_promised_date -- instead of a second, drifting copy.
    """
    dialogue.customer_promised_date = interp.promised_date
    cmt_id = f"cmt-{uuid4().hex[:8]}"
    for b in blockers:
        if b.get("status") == "open":
            b["status"] = "closed"
            await supersede_blocker_with_commitment(
                graph_store,
                case.get("invoice_no") or "",
                b["id"],
                cmt_id,
                {"date": interp.promised_date, "type": "payment_date"},
            )
    for c in commitments:
        if c.get("status") == "active":
            c["status"] = "superseded"
    commitments.append(
        {
            "id": cmt_id,
            "case_id": case.get("case_id"),
            "type": "payment_date",
            "date": interp.promised_date,
            "owner": "customer",
            "status": "active",
            "source": "customer",
            "confidence": interp.confidence,
            "miss_consequence": "re-engage or escalate",
        }
    )


async def apply_reply_signal(
    case: Dict[str, Any],
    text: str,
    ledger,
    *,
    now: datetime,
    interpreter=None,
    graph_store=None,
) -> InterpretedReply:
    interp = (interpreter or DEFAULT_INTERPRETER).interpret(text, now=now)
    dialogue = DialogueSnapshot.from_dict(case.get("dialogue") or {})
    dialogue.latest_inbound = text
    dialogue.reply_type = interp.reply_type
    dialogue.sentiment = interp.sentiment
    dialogue.interpretation_confidence = interp.confidence
    dialogue.awaiting_interpretation = False
    if interp.needs_clarification:
        dialogue.interpretation_confidence = min(dialogue.interpretation_confidence, 0.4)
        dialogue.open_questions = ["What concrete payment or follow-up date?"]

    # Explicit customer asks (added 2026-08-04): these ride alongside
    # whatever reply_type got classified above, so they're applied
    # unconditionally rather than inside the reply_type branch below --
    # a blocker reply and a contact-redirect request can arrive in the
    # same message.
    if interp.mentioned_contact_email and "@" in interp.mentioned_contact_email:
        case["customer_email"] = interp.mentioned_contact_email
    if interp.mentioned_contact_name:
        case["customer_name"] = interp.mentioned_contact_name
    if interp.special_instruction:
        dialogue.special_instructions = (
            dialogue.special_instructions + [interp.special_instruction]
        )[-3:]
    # Flip from PM-directed to customer-directed only on explicit
    # authorization or a distinct customer contact being given -- added
    # 2026-08-06. "Let me check, I'll get back to you" must NOT flip this;
    # see authorizes_customer_contact's docstring.
    if case.get("target") == "pm" and (
        interp.authorizes_customer_contact
        or (interp.mentioned_contact_email and "@" in interp.mentioned_contact_email)
    ):
        case["target"] = "customer"

    budget = AutonomyBudget.from_dict(case.get("budget") or {})
    reset_unanswered_on_reply(budget)

    state = case.get("state") or "overdue"
    commitments = list(case.get("commitments") or [])
    blockers = list(case.get("blockers") or [])
    world = WorldSnapshot.from_dict(case.get("world") or {})

    if interp.reply_type == "unsubscribe":
        state = transition(state, "opt_out")
        world.consent_ok = False
        world.status = "opted_out"
    elif interp.reply_type == "dispute":
        # Fixed 2026-09-03 (Change 2): a dispute reply is the CUSTOMER'S
        # CLAIM, not a world-truth fact -- writing world.status = "disputed"
        # here recorded an unverified assertion straight into world truth,
        # contradicting this codebase's own P1 principle (world_model.py:1,
        # "World truth fields only -- never inferred from conversation
        # alone") and inconsistent with how a paid-claim is already handled
        # (dialogue.customer_claimed_paid = True, never world.status =
        # "paid"). Mirror that pattern instead. Escalation and
        # send-suppression are unaffected: transition(state, "dispute") is
        # a hard rule (state_machine.py) that always returns DISPUTED
        # regardless of from_state, and both goals.choose_objective
        # ("world.is_disputed or state == 'disputed'") and
        # guardrails.dispute_check ("world.get('status') == 'disputed' or
        # state == 'disputed'") already gate on case state as well as world
        # status, so they still escalate / suppress the send off
        # case["state"] alone -- verified by tracing both call sites.
        state = transition(state, "dispute")
        dialogue.customer_claimed_disputed = True
    elif interp.reply_type == "hostile":
        # Fixed 2026-09-03: hostile replies had no branch here and fell
        # through to the generic `else` below (state="customer_responded"),
        # which choose_objective/choose_tactic then treat as an ordinary
        # engaged customer -- confidence 0.8 also clears the <0.55
        # clarify_date safety-net in goals.py, so it fell all the way to the
        # default establish_contact objective and offered a chase tactic
        # (polite_outreach/soft_nudge/firm_reminder) in reply to hostility.
        # Mirror the dispute branch above: drive state via transition() to
        # the deterministic state machine's hostile -> escalation_required
        # rule so goals.choose_objective's existing escalation branch takes
        # over. No world.status write here -- hostility is a dialogue fact
        # about how the customer is behaving, not a world-truth fact about
        # the invoice (see world_model.py:1), so it must not be conflated
        # with e.g. "disputed".
        state = transition(state, "hostile")
        if interp.promised_date:
            # Fixed 2026-09-03 (Change 1): a hostile reply can still name a
            # payment date ("This is ridiculous. We'll pay on the 15th.").
            # Record it as a normal active payment_date commitment (same
            # helper the payment_date branch below uses) so it isn't lost --
            # escalation still wins for routing, state stays
            # ESCALATION_REQUIRED (set just above), never promise_to_pay.
            await _record_payment_date_commitment(case, dialogue, commitments, blockers, graph_store, interp)
    elif interp.reply_type == "handoff":
        # Added 2026-09-03: the outbound disclosure line ("...just reply
        # and a member of the team will pick this up") means customers now
        # reply asking for a human, or asking outright whether they're
        # talking to a bot. Same mechanism as the hostile branch just
        # above -- drive state via transition() so the deterministic
        # state_machine's "hostile" -> ESCALATION_REQUIRED hard rule takes
        # over (reused rather than adding a second event/rule, since a
        # handoff request needs exactly the same "stop chasing, hand off
        # to a human" outcome hostility does) and goals.choose_objective's
        # existing escalation branch routes to escalate_handoff instead of
        # a chase tactic. No world.status write here -- like hostility,
        # "the customer wants a human" is a dialogue fact about this
        # conversation, not a world-truth fact about the invoice (see
        # world_model.py:1), so it must not be conflated with e.g.
        # "disputed".
        state = transition(state, "hostile")
    elif interp.reply_type == "paid_claim":
        dialogue.customer_claimed_paid = True
        state = transition(state, "paid_claim") if state != "customer_responded" else transition("customer_responded", "paid_claim")
        if state == case.get("state"):
            state = "waiting_for_customer"
    elif interp.reply_type == "payment_date" and interp.promised_date and interp.promised_date < now.date().isoformat():
        # Fix 4 (2026-08-20, FIX_PLAN_commitment_grounding.md): a
        # payment_date commitment whose date is already past at creation
        # time was previously accepted silently, flipping state straight to
        # promise_to_pay as if a genuine forward-looking promise had been
        # made. Two real cases land here: a demo/test fixture date the sim
        # clock has already passed, and a genuine production reply like "we
        # paid on the 18th" arriving after the 18th -- that's really a
        # paid-claim needing verification, not a forward commitment to
        # track. Route to clarification instead of fabricating a
        # forward-looking commitment out of an elapsed date; no commitment
        # is created and no existing one is superseded.
        dialogue.customer_promised_date = interp.promised_date
        dialogue.interpretation_confidence = min(dialogue.interpretation_confidence, 0.4)
        dialogue.open_questions = [
            f"Promised date {interp.promised_date} is already past -- did you already send "
            "payment, or did you mean a different (future) date?"
        ]
        state = "customer_responded"
    elif interp.reply_type == "payment_date" and interp.promised_date:
        await _record_payment_date_commitment(case, dialogue, commitments, blockers, graph_store, interp)
        state = "promise_to_pay"
    elif interp.reply_type == "blocker":
        blk_id = f"blk-{uuid4().hex[:8]}"
        blockers.append(
            {
                "id": blk_id,
                "case_id": case.get("case_id"),
                "type": interp.blocker_type or "other",
                "owner": "customer",
                "description": interp.blocker_description or text[:200],
                "expected_resolution": interp.followup_date,
                "status": "open",
            }
        )
        if interp.followup_date:
            commitments.append(
                {
                    "id": f"cmt-{uuid4().hex[:8]}",
                    "case_id": case.get("case_id"),
                    "type": "follow_up_date",
                    "date": interp.followup_date,
                    "owner": "customer",
                    "status": "active",
                    "source": "customer",
                    "confidence": interp.confidence,
                    "miss_consequence": "re-engage",
                }
            )
            consume_postponement(budget)
        state = "blocked"
    elif interp.reply_type == "checkback":
        if interp.followup_date:
            commitments.append(
                {
                    "id": f"cmt-{uuid4().hex[:8]}",
                    "case_id": case.get("case_id"),
                    "type": "follow_up_date",
                    "date": interp.followup_date,
                    "owner": "customer",
                    "status": "active",
                    "source": "customer",
                    "confidence": interp.confidence,
                    "miss_consequence": "re-engage",
                }
            )
            consume_postponement(budget)
        state = "follow_up_scheduled"
    elif interp.reply_type == "vague":
        state = "customer_responded"
    else:
        state = "customer_responded"

    case["state"] = state
    case["dialogue"] = dialogue.to_dict()
    # preserve needs_clarification in dialogue dict
    case["dialogue"]["interpretation_confidence"] = dialogue.interpretation_confidence
    if interp.needs_clarification:
        case["dialogue"]["awaiting_interpretation"] = False
        case["dialogue"]["open_questions"] = ["What concrete payment or follow-up date?"]
    case["budget"] = budget.to_dict()
    case["commitments"] = commitments
    case["blockers"] = blockers
    case["world"] = world.to_dict()

    await ledger.append(
        case["id"],
        "reply_received",
        {
            "text": text,
            "reply_type": interp.reply_type,
            "confidence": interp.confidence,
            "promised_date": interp.promised_date,
        },
        at=now.isoformat(),
        principles=["P1", "P12"] if interp.reply_type == "paid_claim" else ["P12"],
    )
    return interp


async def apply_payment_signal(case: Dict[str, Any], ledger, *, now: datetime) -> None:
    world = WorldSnapshot.from_dict(case.get("world") or {})
    world.balance_due = 0.0
    world.status = "paid"
    world.paid_at = now.date().isoformat()
    case["world"] = world.to_dict()
    case["state"] = "paid"
    case["next_action_at"] = None
    for c in case.get("commitments") or []:
        if c.get("status") == "active":
            c["status"] = "kept"
    await ledger.append(
        case["id"],
        "payment_posted",
        {"paid_at": world.paid_at},
        at=now.isoformat(),
        principles=["P1", "P5"],
    )


async def apply_dispute_signal(case: Dict[str, Any], ledger, *, now: datetime, note: str = "") -> None:
    world = WorldSnapshot.from_dict(case.get("world") or {})
    world.status = "disputed"
    case["world"] = world.to_dict()
    case["state"] = "disputed"
    case["next_action_at"] = None
    await ledger.append(
        case["id"],
        "dispute_created",
        {"note": note},
        at=now.isoformat(),
        principles=["P1", "P11"],
    )
