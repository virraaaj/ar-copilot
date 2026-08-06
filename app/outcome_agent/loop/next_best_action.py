"""Hierarchical planner — selects among simulated candidates (P8, P9)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from app.outcome_agent.domain.types import CandidateAction
from app.outcome_agent.loop.action_simulator import simulate_candidates


class PlanningAdapter(Protocol):
    def pick(self, candidates: List[CandidateAction], context: Dict[str, Any]) -> CandidateAction: ...


class DeterministicPlanningAdapter:
    def pick(self, candidates: List[CandidateAction], context: Dict[str, Any]) -> CandidateAction:
        if not candidates:
            raise ValueError("no candidates")
        # Prefer goal-stack tactic among top scores
        goal_tactic = (context.get("goal_stack") or {}).get("selected_tactic")
        top = candidates[0]
        for c in candidates:
            if c.tactic == goal_tactic and c.score >= top.score - 0.25:
                return c
        return top


DEFAULT_PLANNER = DeterministicPlanningAdapter()


def plan_next_action(
    context: Dict[str, Any],
    planner: Optional[PlanningAdapter] = None,
) -> tuple[List[CandidateAction], CandidateAction]:
    candidates = simulate_candidates(context)
    adapter = planner or DEFAULT_PLANNER
    selected = adapter.pick(candidates, context)
    return candidates, selected
