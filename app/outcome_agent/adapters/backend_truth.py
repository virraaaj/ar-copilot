"""Production world-truth from BackendClient (paid/balance)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.outcome_agent.domain.world_model import WorldSnapshot


async def refresh_world_from_backend(
    case: Dict[str, Any], backend: Any
) -> WorldSnapshot:
    """Best-effort refresh; seed/demo cases keep seed source if backend fails."""
    world = WorldSnapshot.from_dict(case.get("world") or {})
    case_id = case.get("case_id")
    if not backend or not case_id:
        return world
    try:
        inv = await backend.get_invoice(case_id)
    except Exception:
        return world
    if not inv:
        return world
    open_amount = inv.get("open_amount")
    if open_amount is not None:
        world.balance_due = float(open_amount)
    if world.balance_due <= 0:
        world.status = "paid"
    world.source = "backend"
    world.due_date = inv.get("due_date") or world.due_date
    return world
