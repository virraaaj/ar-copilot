"""Commitment-known north-star metric adapted to CaseStore."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

from app.outcome_agent.domain.types import OPEN_STATES
from app.outcome_agent.store.case_store import CaseStore

_KNOWN_STATES = {
    "promise_to_pay",
    "follow_up_scheduled",
    "blocked",
    "escalation_required",
    "escalated_to_human",
}
_DENOMINATOR = OPEN_STATES - {"paused"}


@dataclass(frozen=True)
class CommitmentMetric:
    total_open: int
    known: int
    unknown: int

    @property
    def known_pct(self) -> float:
        return round(100 * self.known / self.total_open, 1) if self.total_open else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["known_pct"] = self.known_pct
        # Compat with prior UI shape
        d["by_case"] = []
        return d


def _is_known(case: Dict[str, Any]) -> bool:
    state = case.get("state")
    if state in _KNOWN_STATES:
        if state == "blocked":
            blockers = case.get("blockers") or []
            return any(b.get("expected_resolution") for b in blockers if b.get("status") == "open")
        return True
    commitments = case.get("commitments") or []
    return any(c.get("status") == "active" and c.get("date") for c in commitments)


async def compute_commitment_metric(store: CaseStore) -> CommitmentMetric:
    cases = await store.list_all()
    open_cases = [c for c in cases if c.get("state") in _DENOMINATOR]
    known = sum(1 for c in open_cases if _is_known(c))
    return CommitmentMetric(
        total_open=len(open_cases),
        known=known,
        unknown=len(open_cases) - known,
    )
