"""Conversation beliefs — may conflict with world truth (P1)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DialogueSnapshot:
    latest_inbound: Optional[str] = None
    latest_outbound: Optional[str] = None
    reply_type: Optional[str] = None
    sentiment: str = "neutral"
    last_ask_id: Optional[str] = None
    last_ask_tactic: Optional[str] = None
    open_questions: List[str] = field(default_factory=list)
    customer_claimed_paid: bool = False
    customer_promised_date: Optional[str] = None
    interpretation_confidence: float = 1.0
    awaiting_interpretation: bool = False

    def conflicts_with_world_unpaid(self, world_unpaid: bool) -> bool:
        return bool(self.customer_claimed_paid and world_unpaid)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DialogueSnapshot":
        if not data:
            return cls()
        return cls(
            latest_inbound=data.get("latest_inbound"),
            latest_outbound=data.get("latest_outbound"),
            reply_type=data.get("reply_type"),
            sentiment=data.get("sentiment", "neutral"),
            last_ask_id=data.get("last_ask_id"),
            last_ask_tactic=data.get("last_ask_tactic"),
            open_questions=list(data.get("open_questions") or []),
            customer_claimed_paid=bool(data.get("customer_claimed_paid", False)),
            customer_promised_date=data.get("customer_promised_date"),
            interpretation_confidence=float(data.get("interpretation_confidence", 1.0)),
            awaiting_interpretation=bool(data.get("awaiting_interpretation", False)),
        )
