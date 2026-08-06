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
    "pm_awareness_check": "Ask the PM whether they know a payment date before contacting the customer",
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
    contact_target: str = "customer",
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
    # Still waiting on the PM -- a PM reply that isn't a real blocker/promise
    # (e.g. "let me check, I'll get back to you") must not fall through to
    # the customer-engaged/follow-up-due branches below and start chasing
    # the PM for a payment date as if they were the customer. Added
    # 2026-08-06 (user feedback): this was the actual bug -- "checkback"
    # set state=follow_up_scheduled, which unconditionally mapped to
    # obtain_commitment regardless of who replied or whether contact with
    # the customer was ever authorized.
    if contact_target == "pm" and state not in ("blocked", "promise_to_pay", "promise_missed"):
        return "establish_contact", "Still awaiting PM authorization or a date before contacting the customer directly"
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
    is_first_contact: bool = False,
    contact_target: str = "customer",
) -> Tuple[str, str]:
    failed = set(failed_tactics or [])
    weights = tactic_weights or {}

    # Every touch while still PM-directed uses pm_awareness_check, not just
    # the literal first message -- choose_objective already keeps returning
    # establish_contact for as long as contact_target=="pm" (i.e. until the
    # PM authorizes customer contact or provides one), so this fires on
    # every re-check too, not only turn one. is_first_contact is kept as a
    # fallback for callers that don't thread contact_target through.
    # Added 2026-08-06 (user feedback): this was previously missing
    # entirely -- establish_contact fell straight into the generic
    # customer-facing candidates below, so the agent invented a "who's your
    # AP contact" ask on its own with no real PM-first step, and once the
    # PM replied at all (even just "let me check") the next turn jumped
    # straight to chasing them for a payment date like a customer.
    if objective == "establish_contact" and (contact_target == "pm" or is_first_contact) and "pm_awareness_check" not in failed:
        return "pm_awareness_check", TACTICS.get("pm_awareness_check", "Check with PM before contacting customer")

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
    contact_target: str = "customer",
) -> GoalStack:
    objective, obj_rationale = choose_objective(
        state, world, dialogue, budget, uncertainty, has_active_commitment,
        failed_ask_count, contact_target=contact_target,
    )
    tactic, tac_rationale = choose_tactic(
        objective, state, uncertainty, failed_tactics, tactic_weights,
        is_first_contact=dialogue.latest_outbound is None,
        contact_target=contact_target,
    )
    return GoalStack(
        primary_outcome=PRIMARY_OUTCOME,
        current_objective=objective,
        selected_tactic=tactic,
        objective_rationale=f"{obj_rationale}. Tactic: {tac_rationale}",
    )
