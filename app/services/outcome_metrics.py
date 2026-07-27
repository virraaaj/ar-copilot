"""
Outcome-agent north-star metric (added 2026-07-25, per the "long-horizon
outcome agent" spec): what fraction of active chases have a KNOWN next
commitment, vs. still an open question mark. This is meant to be the
product's real success signal -- not "emails sent" -- so it's computed
directly from chase_store state rather than living only as an
aspirational config value.

A chase counts as "known" when it's in one of:
  - commitment_tracked / verifying_payment -- an actual payment date is
    on record (or a payment claim is being verified)
  - escalated -- a human is assigned. Covers both "dispute routed" and
    "no reply, escalate": both mean the case has a concrete owner instead
    of sitting in automated limbo.
  - blocked with a blocker_resolution_date on record -- a concrete reason
    for the delay AND a date it's expected to clear (the spec's "blocker
    plus expected blocker resolution date" commitment type).
  - any awaiting_* / pending state whose most recent event is a
    checkback_scheduled with a real followup_date -- the customer agreed
    to a specific follow-up date, even though no payment date exists yet.

Everything else open (pending with no history, a plain unanswered
awaiting_*, or paused) is "unknown" -- the whole point of the metric is
to surface that instead of letting it hide inside "the chase is open."

paused is excluded from the denominator entirely: a human intentionally
took it out of automated pursuit, so it isn't a live "is this advancing"
question the way the rest of OPEN_STATES are.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from app.services.chase_store import ChaseStore, OPEN_STATES

_KNOWN_STATES = {"commitment_tracked", "verifying_payment", "escalated"}
_AMBIGUOUS_STATES = {"pending", "awaiting_pm", "awaiting_customer", "awaiting_contact"}
_DENOMINATOR_STATES = tuple(s for s in OPEN_STATES if s != "paused")


@dataclass(frozen=True)
class CommitmentMetric:
    total_open: int
    known: int
    unknown: int

    @property
    def known_pct(self) -> float:
        return round(100 * self.known / self.total_open, 1) if self.total_open else 0.0


async def _has_scheduled_followup(chase_store: ChaseStore, chase_id: str) -> bool:
    """True if the most recent thing that happened on this chase was us
    scheduling a checkback with a real date -- if a new outreach or reply
    happened since, the conversation moved on and that old date is stale."""
    events = await chase_store.list_events(chase_id)
    for e in reversed(events):
        if e["kind"] in ("outreach_sent", "dry_run_send", "reply_received"):
            return False
        if e["kind"] == "checkback_scheduled":
            detail = e.get("detail") or {}
            return bool(detail.get("followup_date"))
    return False


async def _is_known(chase_store: ChaseStore, chase: Dict[str, Any]) -> bool:
    if chase["state"] in _KNOWN_STATES:
        return True
    if chase["state"] == "blocked":
        return bool(chase.get("blocker_resolution_date"))
    if chase["state"] in _AMBIGUOUS_STATES:
        return await _has_scheduled_followup(chase_store, chase["id"])
    return False


async def compute_commitment_metric(chase_store: ChaseStore) -> CommitmentMetric:
    chases = await chase_store.list_all()
    open_chases = [c for c in chases if c["state"] in _DENOMINATOR_STATES]
    known = 0
    for c in open_chases:
        if await _is_known(chase_store, c):
            known += 1
    return CommitmentMetric(total_open=len(open_chases), known=known, unknown=len(open_chases) - known)


async def commitment_breakdown(chase_store: ChaseStore) -> List[Dict[str, Any]]:
    """Per-chase detail backing a UI list of exactly which open invoices
    still lack a known next commitment, so it's actionable rather than
    just a number."""
    chases = await chase_store.list_all()
    out: List[Dict[str, Any]] = []
    for c in chases:
        if c["state"] not in _DENOMINATOR_STATES:
            continue
        out.append({
            "chase_id": c["id"],
            "case_id": c["case_id"],
            "invoice_no": c.get("invoice_no"),
            "project_number": c.get("project_number"),
            "state": c["state"],
            "known_commitment": await _is_known(chase_store, c),
        })
    return out
