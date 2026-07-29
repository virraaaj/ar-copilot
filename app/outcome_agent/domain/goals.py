"""Hierarchical goals: Outcome → Objective → Tactic (P8)."""
from __future__ import annotations

from typing import List, Optional, Tuple

from app.outcome_agent.domain.dialogue_model import DialogueSnapshot
from app.outcome_agent.domain.types import AutonomyBudget, GoalStack, Uncertainty
from app.outcome_agent.domain.world_model import WorldSnapshot

PRIMARY_OUTCOME = "Collect outstanding balance while preserving relationship"

OBJECTIVES = {
    "establish_contact": "Get a first substantive reply",
    "obtain_commitment": "Secure a payment or follow-up date",
    "verify_payment": "Confirm claimed payment against world truth",
    "clarify_date": "Ask one concrete clarifying question",
    "resolve_blocker": "Track blocker to expected resolution",
    "recover_missed_promise": "Re-engage after a missed promise",
    "escalate_handoff": "Hand off with an escalation dossier",
    "suppress_collections": "Exit collections loop",
}

TACTICS = {
    "polite_outreach": "Friendly first touch asking for status",
    "soft_nudge": "Gentle follow-up after silence",
    "firm_reminder": "Clear reminder with invoice/amount",
    "clarify_ask": "Ask exactly one clarifying question",
    "confirm_promise": "Acknowledge and calendar the promise",
    "blocker_ack": "Acknowledge blocker + resolution date",
    "verify_payment_ask": "Request remittance / payment proof",
    "reflexion_reask": "Different ask after failed prior tactic",
    "escalation_pack": "Build human handoff dossier",
    "dispute_route": "Route dispute out of collections",
}


def choose_objective(
    state: str,
    world: WorldSnapshot,
    dialogue: DialogueSnapshot,
    budget: AutonomyBudget,
    uncertainty: Uncertainty,
    has_active_commitment: bool,
    failed_ask_count_for_objective: int = 0,
) -> Tuple[str, str]:
    """Return (objective, rationale). Deterministic hierarchy."""
    if world.is_paid:
        return "suppress_collections", "World balance is paid"
    if world.opted_out:
        return "suppress_collections", "Contact opted out"
    if world.is_disputed or state == "disputed":
        return "suppress_collections", "Invoice disputed — exit collections"
    if budget.exhausted() or state in ("escalation_required", "escalated_to_human"):
        return "escalate_handoff", "Autonomy budget exhausted or escalation required"
    if dialogue.conflicts_with_world_unpaid(world.conflicts_with_paid_claim()):
        return "verify_payment", "Customer claimed paid but world still shows balance (P1)"
    if uncertainty.needs_clarification or uncertainty.confidence < 0.55:
        return "clarify_date", "Low confidence — ask one thing (P7)"
    if state == "promise_missed":
        return "recover_missed_promise", "Prior promise missed — change approach (P13)"
    if state == "blocked":
        return "resolve_blocker", "Active blocker with expected resolution"
    if has_active_commitment and state == "promise_to_pay":
        return "obtain_commitment", "Tracking active payment commitment (P2)"
    if failed_ask_count_for_objective >= 2:
        return "clarify_date", "Prior asks failed twice — reflexion changes objective (P13)"
    if state in ("overdue", "due", "outreach_ready", "waiting_for_customer"):
        if dialogue.latest_inbound:
            return "obtain_commitment", "Customer engaged — secure a date"
        return "establish_contact", "No active commitment yet"
    if state == "follow_up_scheduled":
        return "obtain_commitment", "Follow-up due — seek payment date"
    return "establish_contact", "Default outreach objective"


def choose_tactic(
    objective: str,
    state: str,
    uncertainty: Uncertainty,
    failed_tactics: Optional[List[str]] = None,
    tactic_weights: Optional[dict] = None,
) -> Tuple[str, str]:
    failed = set(failed_tactics or [])
    weights = tactic_weights or {}

    candidates: List[str]
    if objective == "verify_payment":
        candidates = ["verify_payment_ask"]
    elif objective == "clarify_date":
        candidates = ["clarify_ask"]
    elif objective == "escalate_handoff":
        candidates = ["escalation_pack"]
    elif objective == "suppress_collections":
        candidates = ["dispute_route"] if state == "disputed" else ["escalation_pack"]
    elif objective == "resolve_blocker":
        candidates = ["blocker_ack", "soft_nudge"]
    elif objective == "recover_missed_promise":
        candidates = ["reflexion_reask", "firm_reminder", "clarify_ask"]
    elif objective == "obtain_commitment":
        candidates = ["confirm_promise", "clarify_ask", "firm_reminder"]
    else:
        candidates = ["polite_outreach", "soft_nudge", "firm_reminder"]

    # Downrank failed tactics (P13 / P10)
    scored = []
    for t in candidates:
        if t in failed and t != "escalation_pack":
            score = -1.0 + weights.get(t, 0.0)
        else:
            score = 1.0 + weights.get(t, 0.0)
        scored.append((score, t))
    scored.sort(reverse=True)
    best = scored[0][1]
    rationale = TACTICS.get(best, best)
    if best in failed:
        rationale = f"Reflexion: avoiding failed tactics {sorted(failed)}; chose {best}"
    return best, rationale


def build_goal_stack(
    state: str,
    world: WorldSnapshot,
    dialogue: DialogueSnapshot,
    budget: AutonomyBudget,
    uncertainty: Uncertainty,
    has_active_commitment: bool,
    failed_tactics: Optional[List[str]] = None,
    tactic_weights: Optional[dict] = None,
    failed_ask_count: int = 0,
) -> GoalStack:
    objective, obj_rationale = choose_objective(
        state, world, dialogue, budget, uncertainty, has_active_commitment, failed_ask_count
    )
    tactic, tac_rationale = choose_tactic(
        objective, state, uncertainty, failed_tactics, tactic_weights
    )
    return GoalStack(
        primary_outcome=PRIMARY_OUTCOME,
        current_objective=objective,
        selected_tactic=tactic,
        objective_rationale=f"{obj_rationale}. Tactic: {tac_rationale}",
    )
