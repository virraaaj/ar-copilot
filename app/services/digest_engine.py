"""
Weekly AR-health digest for Teams project chats (added 2026-07-17): AR
health for the project (open exposure, overdue count/amount, stage mix)
plus a payment-pattern projection per customer, derived from that
customer's own closed-case history. Sent once per project per ISO week
(digest_store.py's dedup), with week-over-week trend against the last
snapshot.

Projection methodology, stated plainly because it's an estimate, not a
guarantee: the real backend has no dedicated "paid_at"/"resolved_at"
field on a case (verified against live UAT data this session) -- the
closest available signal is `updated_at`, the case's last-modified
timestamp, which for a closed_paid case is a reasonable proxy for when it
was actually resolved. `avg_days_relative_to_due` is the mean of
(resolved_date - due_date) across a customer's closed cases: positive
means they historically pay late, negative means early, near zero means
on time. Small sample sizes are surfaced (`sample_size`), not hidden --
a customer with one or two closed cases gets a low-confidence projection,
not a false-confidence one.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.channels.teams import cards
from app.channels.teams.messenger import TeamsMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.services.backend_client import BackendClient
from app.services.digest_store import DigestStore, current_period_key

logger = logging.getLogger(__name__)

MAX_CUSTOMERS_SHOWN = 3


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def compute_ar_health(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`cases` is every case for a project regardless of status; AR health
    itself is scoped to currently-active ones -- a closed_paid case isn't
    open exposure anymore."""
    today = date.today()
    active = [c for c in cases if c.get("case_status") == "active"]

    overdue_amount = 0.0
    overdue_count = 0
    by_stage: Dict[str, int] = {}
    total_open_amount = 0.0

    for c in active:
        amt = c.get("primary_invoice_open_amount") or 0
        total_open_amount += amt
        stage = c.get("current_stage_code") or "unknown"
        by_stage[stage] = by_stage.get(stage, 0) + 1

        due_date = _parse_date(c.get("primary_invoice_due_date"))
        if due_date and due_date < today:
            overdue_count += 1
            overdue_amount += amt

    return {
        "open_invoice_count": len(active),
        "total_open_amount": round(total_open_amount, 2),
        "overdue_count": overdue_count,
        "overdue_amount": round(overdue_amount, 2),
        "by_stage": by_stage,
    }


def _resolution_days(case: Dict[str, Any]) -> Optional[int]:
    due_date = _parse_date(case.get("primary_invoice_due_date"))
    resolved = case.get("updated_at")
    if not due_date or not resolved:
        return None
    try:
        resolved_dt = resolved if isinstance(resolved, datetime) else datetime.fromisoformat(str(resolved))
    except ValueError:
        return None
    return (resolved_dt.date() - due_date).days


def compute_customer_projection(closed_cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    samples = [d for d in (_resolution_days(c) for c in closed_cases) if d is not None]
    if not samples:
        return {"sample_size": 0, "avg_days_relative_to_due": None, "risk": "unknown"}
    avg = sum(samples) / len(samples)
    risk = "high" if avg > 14 else "medium" if avg > 0 else "low"
    return {"sample_size": len(samples), "avg_days_relative_to_due": round(avg, 1), "risk": risk}


def _compute_trend(current: Dict[str, Any], last_snapshot: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not last_snapshot:
        return None
    return {
        "amount_delta": round(current["total_open_amount"] - last_snapshot["total_open_amount"], 2),
        "count_delta": current["open_invoice_count"] - last_snapshot["open_invoice_count"],
        "overdue_delta": current["overdue_count"] - last_snapshot["overdue_count"],
    }


async def send_project_digests(
    backend: BackendClient,
    messenger: TeamsMessenger,
    project_conversation_store: ProjectConversationStore,
    digest_store: DigestStore,
) -> int:
    """Returns how many digests were actually sent this pass. One per
    project per ISO week, for every project with a known Teams chat."""
    sent_count = 0
    period_key = current_period_key()

    for mapping in await project_conversation_store.list_all():
        project_number = mapping["project_number"]
        conversation_id = mapping["conversation_id"]

        if await digest_store.already_sent_this_period(project_number, period_key):
            continue

        try:
            cases = await backend.list_cases(project_id=project_number, limit=500)
        except Exception:
            logger.warning("Could not load cases for project %s -- skipping this week's digest", project_number)
            continue
        if not cases:
            continue

        project_name = next((c.get("project_name") for c in cases if c.get("project_name")), project_number)
        health = compute_ar_health(cases)
        last_snapshot = await digest_store.get_last_snapshot(project_number)
        trend = _compute_trend(health, last_snapshot)

        active_customer_ids = sorted(
            {c.get("customer_id") for c in cases if c.get("case_status") == "active" and c.get("customer_id")}
        )
        projections = []
        for customer_id in active_customer_ids[:MAX_CUSTOMERS_SHOWN]:
            try:
                closed = await backend.list_cases(customer_id=customer_id, case_status="closed_paid", limit=50)
            except Exception:
                continue
            proj = compute_customer_projection(closed)
            proj["customer_id"] = customer_id
            projections.append(proj)

        card = cards.digest_card(project_name=project_name, health=health, trend=trend, projections=projections)
        await messenger.send_card(conversation_id, card)
        await digest_store.record_snapshot(
            project_number, period_key, health["total_open_amount"], health["open_invoice_count"], health["overdue_count"]
        )
        sent_count += 1

    return sent_count
