"""Communication generator Protocol + templates (P12)."""
from __future__ import annotations

from typing import Any, Dict, Protocol

# Tactics whose recipient is the PM/internal owner, not the customer. Added
# 2026-08-06 (user feedback): a brand-new case's very first outreach must go
# to the PM asking whether they already know a payment date, or whether the
# agent should go ahead and contact the customer directly -- not straight to
# the customer, and not a generic "who's your AP contact" ask either.
PM_DIRECTED_TACTICS = {"pm_awareness_check"}


def resolve_recipient(case: Dict[str, Any], tactic: str) -> str:
    """Single source of truth for who a given tactic's email goes to.
    Previously every send path (traced_loop.py, executor.py) read
    case["customer_email"] unconditionally, so there was no way for a
    PM-directed tactic to actually reach the PM even if one existed."""
    world = case.get("world") or {}
    if tactic in PM_DIRECTED_TACTICS:
        return case.get("pm_email") or world.get("internal_owner") or case.get("customer_email") or "pm@example.com"
    return case.get("customer_email") or case.get("pm_email") or "customer@example.com"


class CommunicationGenerator(Protocol):
    def generate(self, case: Dict[str, Any], tactic: str, objective: str) -> str: ...


class TemplateCommunicationGenerator:
    def generate(self, case: Dict[str, Any], tactic: str, objective: str) -> str:
        inv = case.get("invoice_no") or case.get("case_key") or "the invoice"
        amt = case.get("amount")
        amt_s = f"${amt:,.0f}" if isinstance(amt, (int, float)) and amt else "the open balance"
        customer = case.get("customer_name") or "there"

        templates = {
            "pm_awareness_check": (
                f"Hi, invoice {inv} ({amt_s}) is currently open. Are you already aware of "
                f"an expected payment date for this one, or should we go ahead and reach "
                f"out to the customer directly to get an update?"
            ),
            "polite_outreach": (
                f"Hi {customer}, here are the details on the open invoice we're tracking: "
                f"{inv} for {amt_s}. Could you give us an update on where things stand, "
                f"including an expected payment date?"
            ),
            "soft_nudge": (
                f"Following up on {inv} ({amt_s}) — any update on timing?"
            ),
            "firm_reminder": (
                f"Reminder: {inv} for {amt_s} remains open. "
                f"Please confirm a payment date."
            ),
            "clarify_ask": (
                f"Thanks for the note on {inv}. To keep this moving, "
                f"could you confirm one concrete payment or follow-up date?"
            ),
            "confirm_promise": (
                f"Thanks — I've recorded your payment commitment for {inv}. "
                f"I'll check in if anything changes."
            ),
            "blocker_ack": (
                f"Understood on the blocker for {inv}. I'll follow up on the expected resolution date."
            ),
            "verify_payment_ask": (
                f"Thanks — our records still show {inv} with an open balance of {amt_s}. "
                f"Could you share remittance details so we can verify?"
            ),
            "reflexion_reask": (
                f"Circling back on {inv} with a different ask: what date can we expect payment?"
            ),
            "escalation_pack": (
                f"Internal: escalating {inv} ({amt_s}) — autonomy budget exhausted. "
                f"Recommended human move: call AP contact and confirm ownership."
            ),
            "dispute_route": (
                f"We've logged a dispute for {inv} and paused collections outreach."
            ),
        }
        # Intentionally bad draft used only by S7 harness when tactic is force_threat
        if tactic == "force_threat":
            return (
                f"Pay {inv} now or we will sue and send this to a collections agency."
            )
        return templates.get(tactic, templates["polite_outreach"])


DEFAULT_GENERATOR = TemplateCommunicationGenerator()
