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
        state = transition(state, "dispute")
        world.status = "disputed"
    elif interp.reply_type == "paid_claim":
        dialogue.customer_claimed_paid = True
        state = transition(state, "paid_claim") if state != "customer_responded" else transition("customer_responded", "paid_claim")
        if state == case.get("state"):
            state = "waiting_for_customer"
    elif interp.reply_type == "payment_date" and interp.promised_date:
        dialogue.customer_promised_date = interp.promised_date
        cmt_id = f"cmt-{uuid4().hex[:8]}"
        # close open blockers (memory supersession)
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
