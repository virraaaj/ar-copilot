"""Score candidate actions before selection (P9)."""
from __future__ import annotations

from typing import Any, Dict, List

from app.outcome_agent.domain.types import CandidateAction
from app.outcome_agent.loop.communication import DEFAULT_GENERATOR


def simulate_candidates(context: Dict[str, Any]) -> List[CandidateAction]:
    goals = context.get("goal_stack") or {}
    objective = goals.get("current_objective", "establish_contact")
    primary_tactic = goals.get("selected_tactic", "polite_outreach")
    uncertainty = context.get("uncertainty") or {}
    budgets = context.get("budgets") or {}
    conflict = bool(context.get("world_dialogue_conflict"))
    failed = {f.get("tactic") for f in (context.get("failed_asks") or [])}

    # Build a small legal candidate set
    pool = [
        (primary_tactic, objective, 1.0),
        ("clarify_ask", "clarify_date", 0.7),
        ("soft_nudge", "establish_contact", 0.6),
        ("firm_reminder", "obtain_commitment", 0.55),
        ("verify_payment_ask", "verify_payment", 0.5),
        ("escalation_pack", "escalate_handoff", 0.4),
    ]

    scored: List[CandidateAction] = []
    for i, (tactic, obj, base) in enumerate(pool):
        score = base
        principles = ["P9", "P8"]
        rationale_parts = [f"base={base}"]

        if conflict and tactic == "verify_payment_ask":
            score += 1.2
            principles.append("P1")
            rationale_parts.append("world/dialogue conflict boost")
        if uncertainty.get("needs_clarification") and tactic == "clarify_ask":
            score += 1.0
            principles.append("P7")
            rationale_parts.append("uncertainty prefers clarify")
        if budgets.get("exhausted") and tactic == "escalation_pack":
            score += 1.5
            principles.extend(["P3", "P11"])
            rationale_parts.append("budget exhausted")
        if tactic in failed:
            score -= 0.9
            principles.append("P13")
            rationale_parts.append("failed-ask penalty")
        if obj != objective and tactic != primary_tactic:
            score -= 0.15
        if tactic == "firm_reminder" and uncertainty.get("confidence", 1) < 0.55:
            score -= 0.5
            rationale_parts.append("harsh reminder penalized under uncertainty")

        # Suppress illegal
        if "send_message" not in (context.get("allowed_actions") or []) and tactic not in (
            "escalation_pack",
            "dispute_route",
        ):
            score = -10

        case_stub = {
            "invoice_no": context.get("invoice_no"),
            "amount": (context.get("world") or {}).get("balance_due"),
            "customer_name": (context.get("world") or {}).get("customer_name"),
        }
        draft = DEFAULT_GENERATOR.generate(case_stub, tactic, obj)
        scored.append(
            CandidateAction(
                action_id=f"cand-{i}-{tactic}",
                kind="send_message" if tactic != "escalation_pack" else "escalate",
                tactic=tactic,
                objective=obj,
                score=round(score, 3),
                rationale="; ".join(rationale_parts),
                draft_text=draft,
                expected_outcome=_expected(tactic),
                principles=sorted(set(principles)),
            )
        )

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored


def _expected(tactic: str) -> str:
    return {
        "clarify_ask": "concrete_date",
        "verify_payment_ask": "remittance_or_correction",
        "escalation_pack": "human_handoff",
        "confirm_promise": "tracked_commitment",
        "soft_nudge": "reply",
        "firm_reminder": "reply_or_escalation_path",
    }.get(tactic, "engagement")
