"""Core domain types for the Long-Horizon Outcome Agent."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class CaseState(str, Enum):
    NOT_DUE = "not_due"
    DUE = "due"
    OVERDUE = "overdue"
    OUTREACH_READY = "outreach_ready"
    WAITING_FOR_CUSTOMER = "waiting_for_customer"
    CUSTOMER_RESPONDED = "customer_responded"
    BLOCKED = "blocked"
    FOLLOW_UP_SCHEDULED = "follow_up_scheduled"
    PROMISE_TO_PAY = "promise_to_pay"
    PROMISE_MISSED = "promise_missed"
    DISPUTED = "disputed"
    SUPPRESSED = "suppressed"
    ESCALATION_REQUIRED = "escalation_required"
    ESCALATED_TO_HUMAN = "escalated_to_human"
    PAID = "paid"
    CLOSED = "closed"
    PAUSED = "paused"


OPEN_STATES = frozenset(
    {
        CaseState.NOT_DUE.value,
        CaseState.DUE.value,
        CaseState.OVERDUE.value,
        CaseState.OUTREACH_READY.value,
        CaseState.WAITING_FOR_CUSTOMER.value,
        CaseState.CUSTOMER_RESPONDED.value,
        CaseState.BLOCKED.value,
        CaseState.FOLLOW_UP_SCHEDULED.value,
        CaseState.PROMISE_TO_PAY.value,
        CaseState.PROMISE_MISSED.value,
        CaseState.ESCALATION_REQUIRED.value,
        CaseState.ESCALATED_TO_HUMAN.value,
        CaseState.PAUSED.value,
    }
)

TERMINAL_STATES = frozenset(
    {
        CaseState.DISPUTED.value,
        CaseState.SUPPRESSED.value,
        CaseState.PAID.value,
        CaseState.CLOSED.value,
    }
)

COLLECTIONS_SUPPRESSED_STATES = frozenset(
    {
        CaseState.DISPUTED.value,
        CaseState.SUPPRESSED.value,
        CaseState.PAID.value,
        CaseState.CLOSED.value,
        CaseState.ESCALATED_TO_HUMAN.value,
        CaseState.PAUSED.value,
    }
)


class CommitmentType(str, Enum):
    PAYMENT_DATE = "payment_date"
    FOLLOW_UP_DATE = "follow_up_date"
    BLOCKER_RESOLUTION_DATE = "blocker_resolution_date"


class CommitmentStatus(str, Enum):
    ACTIVE = "active"
    KEPT = "kept"
    MISSED = "missed"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class ReplyType(str, Enum):
    PAYMENT_DATE = "payment_date"
    CHECKBACK = "checkback"
    BLOCKER = "blocker"
    PAID_CLAIM = "paid_claim"
    DISPUTE = "dispute"
    UNSUBSCRIBE = "unsubscribe"
    VAGUE = "vague"
    HOSTILE = "hostile"
    HANDOFF = "handoff"
    UNKNOWN = "unknown"


@dataclass
class Commitment:
    id: str
    case_id: str
    type: str
    date: str
    owner: str
    status: str = CommitmentStatus.ACTIVE.value
    source: str = "customer"
    confidence: float = 0.8
    miss_consequence: str = "re-engage or escalate"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Blocker:
    id: str
    case_id: str
    type: str
    owner: str
    description: str
    expected_resolution: Optional[str] = None
    status: str = "open"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AutonomyBudget:
    max_unanswered: int = 3
    unanswered_used: int = 0
    max_postponements: int = 3
    postponements_used: int = 0
    max_missed_promises: int = 3
    misses_used: int = 0
    min_days_between_emails: int = 3
    relationship_risk: float = 0.0

    def unanswered_left(self) -> int:
        return max(0, self.max_unanswered - self.unanswered_used)

    def postponements_left(self) -> int:
        return max(0, self.max_postponements - self.postponements_used)

    def misses_left(self) -> int:
        return max(0, self.max_missed_promises - self.misses_used)

    def exhausted(self) -> bool:
        # 2026-09-03: postponements_used/max_postponements were tracked (see
        # consume_postponement) but never checked here, so a customer could
        # postpone indefinitely and the agent would never escalate.
        return (
            self.unanswered_used >= self.max_unanswered
            or self.misses_used >= self.max_missed_promises
            or self.postponements_used >= self.max_postponements
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["unanswered_left"] = self.unanswered_left()
        d["postponements_left"] = self.postponements_left()
        d["misses_left"] = self.misses_left()
        d["exhausted"] = self.exhausted()
        return d

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "AutonomyBudget":
        if not data:
            return cls()
        return cls(
            max_unanswered=int(data.get("max_unanswered", 3)),
            unanswered_used=int(data.get("unanswered_used", 0)),
            max_postponements=int(data.get("max_postponements", 3)),
            postponements_used=int(data.get("postponements_used", 0)),
            max_missed_promises=int(data.get("max_missed_promises", 3)),
            misses_used=int(data.get("misses_used", 0)),
            min_days_between_emails=int(data.get("min_days_between_emails", 3)),
            relationship_risk=float(data.get("relationship_risk", 0.0)),
        )


@dataclass
class GoalStack:
    primary_outcome: str = "Collect outstanding balance while preserving relationship"
    current_objective: str = "establish_contact"
    selected_tactic: str = "polite_outreach"
    objective_rationale: str = "No active commitment yet"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "GoalStack":
        if not data:
            return cls()
        return cls(
            primary_outcome=data.get(
                "primary_outcome",
                "Collect outstanding balance while preserving relationship",
            ),
            current_objective=data.get("current_objective", "establish_contact"),
            selected_tactic=data.get("selected_tactic", "polite_outreach"),
            objective_rationale=data.get("objective_rationale", ""),
        )


@dataclass
class FailedAsk:
    ask_id: str
    tactic: str
    objective: str
    reason: str
    at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Uncertainty:
    confidence: float = 1.0
    needs_clarification: bool = False
    unclear_fields: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateAction:
    action_id: str
    kind: str
    tactic: str
    objective: str
    score: float
    rationale: str
    draft_text: str = ""
    expected_outcome: str = ""
    principles: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CriticResult:
    passed: bool
    checks: List[Dict[str, Any]] = field(default_factory=list)
    regenerated: bool = False
    requires_human_review: bool = False
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionTrace:
    id: str
    case_id: str
    at: str
    trigger: str
    state_before: str
    state_after: str
    candidates_scored: List[Dict[str, Any]] = field(default_factory=list)
    selected_action: Optional[Dict[str, Any]] = None
    critic_result: Optional[Dict[str, Any]] = None
    policies_checked: List[str] = field(default_factory=list)
    tools_executed: List[str] = field(default_factory=list)
    principles_fired: List[str] = field(default_factory=list)
    explanation: str = ""
    reflexion_note: str = ""
    loop_phase: str = "schedule"
    memory_facts: List[Dict[str, Any]] = field(default_factory=list)
    context_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LearningRecord:
    ask_id: str
    case_id: str
    tactic: str
    outcome: str
    scored_at: str
    produced_commitment_id: Optional[str] = None
    weight_delta: float = 0.0
    objective: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EscalationPack:
    reason: str
    evidence: List[str]
    recommended_human_move: str
    autonomy_suppressed: bool = True
    case_id: str = ""
    invoice_no: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


PRINCIPLES: Dict[str, str] = {
    "P1": "World model ≠ dialogue model",
    "P2": "Commitment-centric planning",
    "P3": "Bounded autonomy",
    "P4": "Critic before act",
    "P5": "Judge after outcome",
    "P6": "Memory as retrieval",
    "P7": "Explicit uncertainty",
    "P8": "Hierarchical goals",
    "P9": "Simulate-then-act",
    "P10": "Delayed credit assignment",
    "P11": "Escalation as designed handoff",
    "P12": "Deterministic skeleton / AI flesh",
    "P13": "Reflexion / self-repair",
}
