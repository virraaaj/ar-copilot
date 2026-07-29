"""Scripted demo scenario packs S1–S8."""
from __future__ import annotations

from typing import Any, Dict, List

SCENARIOS: Dict[str, Dict[str, Any]] = {
    "S1_world_vs_dialogue": {
        "id": "S1_world_vs_dialogue",
        "title": "Customer says paid, world disagrees",
        "principles": ["P1", "P12"],
        "invoice_no": "INV-7104",
        "narration": "Inject paid claim; world stays unpaid; verify_payment objective.",
        "beats": [
            {"op": "inject_reply", "text": "we already paid this invoice last week", "narration": "Watching P1: dialogue claims paid"},
            {"op": "run_tick", "narration": "Planner must choose verify_payment — not close"},
            {"op": "assert", "checks": ["world_unpaid", "objective_verify_payment"]},
        ],
    },
    "S2_commitment_loop": {
        "id": "S2_commitment_loop",
        "title": "Approval → Tuesday follow-up → Friday promise → pay",
        "principles": ["P2", "P6", "P8"],
        "invoice_no": "INV-4821",
        "narration": "Blocker follow-up becomes promise then payment.",
        "beats": [
            {"op": "inject_reply", "text": "Travel approved — check back Tuesday July 28", "narration": "P2/P6: follow-up commitment"},
            {"op": "jump", "date": "2026-07-28", "narration": "Jump to follow-up date"},
            {"op": "inject_reply", "text": "We will pay Friday August 1", "narration": "Promise replaces blocker"},
            {"op": "jump", "date": "2026-08-01", "narration": "Promise due"},
            {"op": "simulate_payment", "narration": "Post payment — terminal paid"},
        ],
    },
    "S3_budget_exhaustion": {
        "id": "S3_budget_exhaustion",
        "title": "Silence until escalation dossier",
        "principles": ["P3", "P11"],
        "invoice_no": "INV-2333",
        "narration": "Advance unanswered caps → escalation pack.",
        "beats": [
            {"op": "run_tick", "narration": "Consume last unanswered budget (P3)"},
            {"op": "run_tick", "narration": "Budget exhausted → escalation dossier (P11)"},
            {"op": "assert", "checks": ["escalation_present"]},
        ],
    },
    "S4_missed_promise_credit": {
        "id": "S4_missed_promise_credit",
        "title": "Riverbend promise miss teaches tactic",
        "principles": ["P5", "P10", "P13"],
        "invoice_no": "INV-8890",
        "narration": "Miss judged → reflexion + learning weight.",
        "beats": [
            {"op": "jump", "date": "2026-07-25", "narration": "Advance past promise date"},
            {"op": "run_tick", "narration": "Post-outcome judge: missed (P5/P10)"},
            {"op": "assert", "checks": ["promise_missed_or_learning", "reflexion_present"]},
        ],
    },
    "S5_dispute_exit": {
        "id": "S5_dispute_exit",
        "title": "Quantities wrong",
        "principles": ["P1", "P11"],
        "invoice_no": "INV-6001",
        "narration": "Dispute reply → terminal disputed.",
        "beats": [
            {"op": "inject_reply", "text": "The quantities are wrong on this invoice — we dispute the charges", "narration": "Dispute exits collections"},
            {"op": "assert", "checks": ["state_disputed"]},
        ],
    },
    "S6_uncertainty_clarify": {
        "id": "S6_uncertainty_clarify",
        "title": "Vague delay",
        "principles": ["P7", "P8", "P9"],
        "invoice_no": "INV-7104",
        "narration": "Vague reply → clarify-date preferred over harsh reminder.",
        "beats": [
            {"op": "inject_reply", "text": "maybe soon, not sure yet", "narration": "Low confidence (P7)"},
            {"op": "run_tick", "narration": "Simulator prefers clarify_ask (P9)"},
            {"op": "assert", "checks": ["objective_clarify_or_tactic_clarify"]},
        ],
    },
    "S7_critic_blocks_threat": {
        "id": "S7_critic_blocks_threat",
        "title": "Bad draft caught",
        "principles": ["P4", "P12"],
        "invoice_no": "INV-7104",
        "narration": "Banned language fails critic → regen → pass.",
        "beats": [
            {"op": "force_bad_draft", "text": "Pay now or we will sue and send this to a collections agency", "narration": "Critic must block banned language (P4)"},
            {"op": "assert", "checks": ["critic_regenerated"]},
        ],
    },
    "S8_memory_supersession": {
        "id": "S8_memory_supersession",
        "title": "Blocker replaced by promise",
        "principles": ["P6"],
        "invoice_no": "INV-4821",
        "narration": "Graph closes BLOCKED_BY; HAS_COMMITMENT active.",
        "beats": [
            {"op": "inject_reply", "text": "Blocker cleared — we promise to pay on 2026-08-01", "narration": "Memory supersession (P6)"},
            {"op": "assert", "checks": ["blocker_closed_commitment_active"]},
        ],
    },
}


def list_scenarios() -> List[Dict[str, Any]]:
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "principles": s["principles"],
            "invoice_no": s["invoice_no"],
            "narration": s["narration"],
            "beat_count": len(s["beats"]),
        }
        for s in SCENARIOS.values()
    ]


def get_scenario(scenario_id: str) -> Dict[str, Any]:
    if scenario_id not in SCENARIOS:
        raise KeyError(scenario_id)
    return SCENARIOS[scenario_id]
