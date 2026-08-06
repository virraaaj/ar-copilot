"""Human-readable principle traces."""
from __future__ import annotations

from typing import Any, Dict, List

from app.outcome_agent.domain.types import PRINCIPLES


def explain_principles(principles: List[str]) -> List[Dict[str, str]]:
    return [{"id": p, "label": PRINCIPLES.get(p, p)} for p in principles]


def explain_decision(trace: Dict[str, Any]) -> str:
    if trace.get("explanation"):
        return trace["explanation"]
    selected = trace.get("selected_action") or {}
    return (
        f"Trigger={trace.get('trigger')}; "
        f"chose {selected.get('tactic')} for {selected.get('objective')} "
        f"(principles: {', '.join(trace.get('principles_fired') or [])})"
    )


def explain_event(case: Dict[str, Any], event: Dict[str, Any]) -> str:
    kind = event.get("kind")
    detail = event.get("detail") or {}
    inv = case.get("invoice_no") or case.get("case_key")
    if kind == "decision":
        return detail.get("explanation") or f"Decision on {inv}"
    if kind == "outreach_sent" or kind == "dry_run_send":
        return f"Outbound to {detail.get('recipient')}: {(detail.get('body') or '')[:120]}"
    if kind == "reply_received":
        return f"Inbound: {(detail.get('text') or '')[:120]}"
    if kind == "escalated":
        return f"Escalated: {detail.get('reason')}"
    if kind == "promise_missed":
        return f"Promise missed on {detail.get('date')}"
    if kind == "seed_loaded":
        return f"Seeded {inv} in state {detail.get('state')}"
    return f"{kind} on {inv}"
