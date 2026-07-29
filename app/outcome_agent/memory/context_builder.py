"""Assembles ContextPacket for the planner (P6, P7, P8)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.outcome_agent.domain.dialogue_model import DialogueSnapshot
from app.outcome_agent.domain.goals import build_goal_stack
from app.outcome_agent.domain.types import AutonomyBudget, Uncertainty
from app.outcome_agent.domain.world_model import WorldSnapshot
from app.outcome_agent.memory.document import recall_policy
from app.outcome_agent.memory.episodic import recall_episodic
from app.outcome_agent.memory.operational import recall_operational
from app.outcome_agent.memory.semantic import recall_semantic
from app.outcome_agent.memory.temporal_graph import recall_graph


def _uncertainty(dialogue: DialogueSnapshot, world: WorldSnapshot) -> Uncertainty:
    conf = dialogue.interpretation_confidence
    needs = dialogue.awaiting_interpretation or conf < 0.55 or dialogue.reply_type == "vague"
    unclear: List[str] = []
    if needs:
        unclear.append("payment_date")
    if dialogue.conflicts_with_world_unpaid(world.conflicts_with_paid_claim()):
        unclear.append("payment_status_conflict")
        conf = min(conf, 0.4)
    note = ""
    if dialogue.customer_claimed_paid and world.conflicts_with_paid_claim():
        note = "Customer said paid; world shows unpaid balance"
    elif needs:
        note = "Ask one concrete clarifying question"
    return Uncertainty(
        confidence=conf,
        needs_clarification=needs,
        unclear_fields=unclear,
        note=note,
    )


async def build_context_packet(
    case: Dict[str, Any],
    events: List[Dict[str, Any]],
    *,
    tactic_weights: Optional[Dict[str, float]] = None,
    graph_store=None,
    policy_config: Any = None,
) -> Dict[str, Any]:
    world = WorldSnapshot.from_dict(case.get("world") or {})
    dialogue = DialogueSnapshot.from_dict(case.get("dialogue") or {})
    budget = AutonomyBudget.from_dict(case.get("budget") or {})
    uncertainty = _uncertainty(dialogue, world)
    commitments = [c for c in (case.get("commitments") or []) if c.get("status") == "active"]
    blockers = [b for b in (case.get("blockers") or []) if b.get("status") == "open"]
    failed_asks = list(case.get("failed_asks") or [])
    failed_tactics = [f.get("tactic") for f in failed_asks if f.get("tactic")]
    weights = tactic_weights or {}

    goals = build_goal_stack(
        case.get("state", "overdue"),
        world,
        dialogue,
        budget,
        uncertainty,
        has_active_commitment=bool(commitments),
        failed_tactics=failed_tactics,
        tactic_weights=weights,
        failed_ask_count=len(failed_asks),
    )

    memory_facts: List[Dict[str, Any]] = []
    memory_facts.extend(recall_operational(case))
    memory_facts.extend(recall_episodic(events))
    memory_facts.extend(recall_semantic(failed_asks, weights))
    memory_facts.extend(await recall_graph(graph_store, case.get("invoice_no") or ""))
    if policy_config is not None:
        memory_facts.extend(
            recall_policy(goals.current_objective.replace("_", " "), policy_config)
        )

    conflict = dialogue.conflicts_with_world_unpaid(world.conflicts_with_paid_claim())
    allowed = ["send_message", "schedule_follow_up", "escalate", "mark_paid", "create_dispute", "noop"]
    if world.is_paid or world.opted_out or world.is_disputed:
        allowed = ["noop", "mark_paid"] if world.is_paid else ["noop"]

    return {
        "case_id": case.get("case_id"),
        "case_row_id": case.get("id"),
        "invoice_no": case.get("invoice_no"),
        "state": case.get("state"),
        "world": world.to_dict(),
        "dialogue": dialogue.to_dict(),
        "world_dialogue_conflict": conflict,
        "recent_events": events[-10:],
        "active_commitments": commitments,
        "active_blockers": blockers,
        "recalled_memory_facts": memory_facts,
        "policy_snippets": [f for f in memory_facts if f.get("layer") == "document"],
        "allowed_actions": allowed,
        "uncertainty": uncertainty.to_dict(),
        "budgets": budget.to_dict(),
        "goal_stack": goals.to_dict(),
        "failed_asks": failed_asks,
        "principles_relevant": _principles_for_packet(conflict, uncertainty, budget, failed_asks),
    }


def _principles_for_packet(conflict, uncertainty, budget, failed_asks) -> List[str]:
    ps = ["P6", "P8", "P12"]
    if conflict:
        ps.append("P1")
    if uncertainty.needs_clarification:
        ps.append("P7")
    if budget.exhausted() or budget.unanswered_left() <= 1:
        ps.append("P3")
    if failed_asks:
        ps.append("P13")
    if any(True for _ in (budget.to_dict(),)):
        ps.append("P2")
    return sorted(set(ps))
