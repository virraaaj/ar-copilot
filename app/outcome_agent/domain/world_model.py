"""World truth fields only — never inferred from conversation alone (P1)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class WorldSnapshot:
    invoice_no: str
    case_id: str
    balance_due: float
    status: str  # open | paid | disputed | opted_out
    due_date: Optional[str] = None
    consent_ok: bool = True
    blackout_active: bool = False
    internal_owner: Optional[str] = None
    risk: str = "medium"
    customer_name: str = ""
    project_number: Optional[str] = None
    amount_original: float = 0.0
    paid_at: Optional[str] = None
    source: str = "seed"  # seed | backend
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_paid(self) -> bool:
        return self.balance_due <= 0 or self.status == "paid"

    @property
    def is_disputed(self) -> bool:
        return self.status == "disputed"

    @property
    def opted_out(self) -> bool:
        return self.status == "opted_out" or not self.consent_ok

    def conflicts_with_paid_claim(self) -> bool:
        return (not self.is_paid) and self.balance_due > 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_paid"] = self.is_paid
        d["is_disputed"] = self.is_disputed
        d["opted_out"] = self.opted_out
        return d

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "WorldSnapshot":
        if not data:
            raise ValueError("WorldSnapshot requires data")
        return cls(
            invoice_no=data.get("invoice_no", ""),
            case_id=data.get("case_id", ""),
            balance_due=float(data.get("balance_due", 0)),
            status=data.get("status", "open"),
            due_date=data.get("due_date"),
            consent_ok=bool(data.get("consent_ok", True)),
            blackout_active=bool(data.get("blackout_active", False)),
            internal_owner=data.get("internal_owner"),
            risk=data.get("risk", "medium"),
            customer_name=data.get("customer_name", ""),
            project_number=data.get("project_number"),
            amount_original=float(data.get("amount_original", data.get("balance_due", 0) or 0)),
            paid_at=data.get("paid_at"),
            source=data.get("source", "seed"),
            extra=dict(data.get("extra") or {}),
        )
