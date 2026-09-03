"""Hierarchical goals: Outcome → Objective → Tactic (P8)."""
from __future__ import annotations

from typing import List, Optional, Tuple

from app.outcome_agent.domain.dialogue_model import DialogueSnapshot
from app.outcome_agent.domain.types import AutonomyBudget, CommitmentType, GoalStack, Uncertainty
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
    # Added 2026-08-20 (FIX_PLAN_commitment_grounding.md, Fix 1): a
    # follow_up_date commitment coming due is NOT a payment promise -- there
    # is nothing to "confirm." The right move is to ask for an update on the
    # thing they said they'd get back to us about, without asserting any
    # payment date (that's what confirm_promise wrongly did -- see below).
    "followup_nudge": "Follow up on a promised check-back date without asserting a payment date",
}


def choose_objective(
    state: str,
    world: WorldSnapshot,
    dialogue: DialogueSnapshot,
    budget: AutonomyBudget,
    uncertainty: Uncertainty,
    active_commitment_type: Optional[str] = None,
    failed_ask_count_for_objective: int = 0,
    contact_target: str = "customer",
) -> Tuple[str, str]:
    """Return (objective, rationale). Deterministic hierarchy.

    `active_commitment_type` (a `CommitmentType` value, or None) replaces the
    old `has_active_commitment: bool` parameter. Added 2026-08-20
    (FIX_PLAN_commitment_grounding.md, Fix 1 -- root cause RC1): collapsing
    "a commitment exists" into a bare boolean erased the difference between
    "they promised to PAY on X" (payment_date) and "they promised to GET BACK
    TO US by X" (follow_up_date) before this function ever saw it, so a
    follow_up_date commitment coming due routed to obtain_commitment exactly
    like a real payment promise would -- and obtain_commitment's top
    candidate, confirm_promise, acknowledges a payment that was never made.
    The LLM then improvised one from the invoice's due_date (the Silverline
    bug: "I've noted that Silverline Manufacturing is expected to pay by
    July..." -- nobody said that). `has_active_commitment` is kept below as a
    derived local for branches where only presence/absence genuinely matters,
    not the type.
    """
    has_active_commitment = active_commitment_type is not None
    if world.is_paid:
        return "suppress_collections", "World balance is paid"
    if world.opted_out:
        return "suppress_collections", "Contact opted out"
    if world.is_disputed or state == "disputed":
        # A dispute needs a human to actually resolve it -- was previously
        # "suppress_collections" (silently stop, no one told). Fixed
        # 2026-08-06 (user feedback): a dispute should escalate for human
        # review, with the PM notified, same as any other escalation.
        return "escalate_handoff", "Invoice disputed — requires human review"
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
    # Narrow carve-out (added 2026-08-06, found during a guided-demo
    # transcript review): a PM conversation that's already had a real
    # promise-and-miss cycle -- e.g. a reflexion_reask just went out --
    # lands in "waiting_for_customer" after that send (the generic
    # post-outreach state transition, not a checkback-specific one). The
    # blanket guard below was resetting that turn straight back to the
    # literal first-touch pm_awareness_check script, which reads as the
    # agent forgetting a conversation it's actively having. Only relaxed
    # for this exact combination (waiting_for_customer + a reply already
    # exists) -- a fresh checkback reply still lands on follow_up_scheduled
    # and is untouched by this change, so "let me check and get back to
    # you" still doesn't get immediately re-nagged.
    already_engaged_waiting = state == "waiting_for_customer" and bool(dialogue.latest_inbound)
    if contact_target == "pm" and state not in ("blocked", "promise_to_pay", "promise_missed") and not already_engaged_waiting:
        return "establish_contact", "Still awaiting PM authorization or a date before contacting the customer directly"
    if state == "promise_missed":
        return "recover_missed_promise", "Prior promise missed — change approach (P13)"
    if state == "blocked":
        return "resolve_blocker", "Active blocker with expected resolution"
    if active_commitment_type == CommitmentType.PAYMENT_DATE.value and state == "promise_to_pay":
        return "obtain_commitment", "Tracking active payment commitment (P2)"
    if failed_ask_count_for_objective >= 2:
        return "clarify_date", "Prior asks failed twice — reflexion changes objective (P13)"
    # has_active_commitment gates obtain_commitment below (not just
    # dialogue.latest_inbound / state alone): obtain_commitment's top
    # candidate is confirm_promise, whose entire job is to acknowledge an
    # existing commitment. Routing here with nothing actually promised
    # left the LLM improvising -- it grabbed the invoice's original
    # due_date and presented it as a payment commitment nobody ever gave
    # (found 2026-08-19 live: a PM handoff reply with no date -- "go ahead
    # and reach out directly" -- has no followup_date, so
    # apply_reply_signal correctly creates no commitment, but state still
    # became "follow_up_scheduled"/"waiting_for_customer" and this
    # function routed to obtain_commitment regardless). No active
    # commitment means there's genuinely nothing to confirm yet -- treat
    # it as a fresh outreach instead.
    # Route by TYPE, not just presence, from here down (2026-08-20, Fix 1):
    # only a payment_date commitment justifies obtain_commitment -- that's
    # the only case where confirm_promise (obtain_commitment's top tactic)
    # is telling the truth. A follow_up_date commitment coming due is a
    # customer saying "I'll get back to you," not a payment promise; routing
    # it through obtain_commitment/confirm_promise is exactly the bug that
    # recurred here (a checkback due on 08-29 -> obtain_commitment ->
    # confirm_promise -> the LLM fabricated a payment date from the
    # invoice's due_date). It stays on establish_contact with the
    # followup_nudge tactic (see TACTICS above) instead.
    if state in ("overdue", "due", "outreach_ready", "waiting_for_customer"):
        if dialogue.latest_inbound and active_commitment_type == CommitmentType.PAYMENT_DATE.value:
            return "obtain_commitment", "Customer engaged — secure a date"
        if dialogue.latest_inbound and active_commitment_type == CommitmentType.FOLLOW_UP_DATE.value:
            return "establish_contact", "Follow-up promised, not a payment date — check in without asserting one"
        return "establish_contact", "No active commitment yet"
    if state == "follow_up_scheduled":
        if active_commitment_type == CommitmentType.PAYMENT_DATE.value:
            return "obtain_commitment", "Follow-up due — seek payment date"
        if active_commitment_type == CommitmentType.FOLLOW_UP_DATE.value:
            return "establish_contact", "Follow-up date reached — no payment date was ever promised, ask for an update"
        return "establish_contact", "Contact authorized/handed off but no date given yet"
    return "establish_contact", "Default outreach objective"


def choose_tactic(
    objective: str,
    state: str,
    uncertainty: Uncertainty,
    failed_tactics: Optional[List[str]] = None,
    tactic_weights: Optional[dict] = None,
    is_first_contact: bool = False,
    contact_target: str = "customer",
    active_commitment_type: Optional[str] = None,
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
        # Hard invariant (2026-08-20, FIX_PLAN_commitment_grounding.md, Fix
        # 1): confirm_promise's entire job is acknowledging an existing
        # PAYMENT promise -- it must be structurally unreachable without one,
        # not just correctly routed around by choose_objective. This is the
        # backstop for the exact bug that recurred here: if obtain_commitment
        # is ever reached again with the wrong (or no) commitment type --
        # whether from a future regression in choose_objective or a new
        # caller that skips it -- this candidate list must not offer a
        # tactic that asserts a payment date nobody gave.
        if active_commitment_type != CommitmentType.PAYMENT_DATE.value:
            candidates = [c for c in candidates if c != "confirm_promise"]
    elif objective == "establish_contact" and active_commitment_type == CommitmentType.FOLLOW_UP_DATE.value:
        # A follow_up_date commitment is due -- ask for an update on what
        # they said they'd get back to us about. followup_nudge (2026-08-20,
        # Fix 1) never asserts a payment date, unlike confirm_promise.
        candidates = ["followup_nudge", "clarify_ask", "soft_nudge"]
    else:
        candidates = ["polite_outreach", "soft_nudge", "firm_reminder"]

    # Downrank failed tactics (P13 / P10)
    scored = []
    for idx, t in enumerate(candidates):
        if t in failed and t != "escalation_pack":
            score = -1.0 + weights.get(t, 0.0)
        else:
            score = 1.0 + weights.get(t, 0.0)
        # -idx as the tie-break (not just (score, t)): with no learned
        # weight yet -- the normal case for a fresh case, or any tie --
        # every candidate here scores identically, and sorting plain
        # (score, tactic_name) tuples in reverse falls back to comparing
        # the tactic string itself: "firm_reminder" alphabetically beats
        # "confirm_promise", so firm_reminder silently won every tie
        # regardless of each objective's own declared candidate order
        # (confirm_promise is listed FIRST for obtain_commitment
        # specifically because acknowledging a fresh promise is the right
        # default, not re-issuing a generic reminder). Found 2026-08-18
        # via user review of the guided demo: the agent kept re-asking a
        # PM "are you aware of a date" immediately after the PM had just
        # given one, because confirm_promise was structurally unreachable
        # whenever weights tied. -idx makes the tie-break follow candidate
        # list order (first-listed wins) instead of the alphabet.
        scored.append((score, -idx, t))
    scored.sort(reverse=True)
    best = scored[0][2]
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
    active_commitment_type: Optional[str] = None,
    failed_tactics: Optional[List[str]] = None,
    tactic_weights: Optional[dict] = None,
    failed_ask_count: int = 0,
    contact_target: str = "customer",
) -> GoalStack:
    objective, obj_rationale = choose_objective(
        state, world, dialogue, budget, uncertainty, active_commitment_type,
        failed_ask_count, contact_target=contact_target,
    )
    tactic, tac_rationale = choose_tactic(
        objective, state, uncertainty, failed_tactics, tactic_weights,
        is_first_contact=dialogue.latest_outbound is None,
        contact_target=contact_target,
        active_commitment_type=active_commitment_type,
    )
    return GoalStack(
        primary_outcome=PRIMARY_OUTCOME,
        current_objective=objective,
        selected_tactic=tactic,
        objective_rationale=f"{obj_rationale}. Tactic: {tac_rationale}",
    )
