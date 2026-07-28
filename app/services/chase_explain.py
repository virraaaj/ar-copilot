"""
Audit and Explainability (long-horizon outcome agent spec §6.19) -- turns
one chase_event (kind + detail, already logged by chase_machine.py's
Decision.events / chase_engine.py's execution) into a plain-English
sentence a non-engineer can read, matching the spec's own worked example:

    "The agent sent this follow-up because the customer said approval was
    pending, but did not provide an approval date or payment date..."

Deliberately template-based, not LLM-generated -- explainability is an
audit function, and the spec's core principle (§4) is that anything
safety/trust-adjacent stays deterministic. Every event this app already
logs carries enough structured detail (kind, target, reason, blocker_
type, ...) to assemble a real sentence without guessing; this module is
purely a formatter over data that already exists, so it can never
"hallucinate" a reason the engine didn't actually have.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

_ESCALATION_REASONS = {
    "dispute": "the reply was a confident, genuine dispute of the invoice",
    "nudge_budget_exhausted": "there was no reply after the maximum number of follow-ups",
    "clarify_budget_exhausted": "the reply still didn't resolve to a clear commitment after a clarifying question",
    "postpone_budget_exhausted": "the contact kept postponing without ever giving a payment date or paying",
    "blocker_postpone_budget_exhausted": "a reported blocker never resolved after repeated check-ins",
    "missed_commitment_budget_exhausted": "too many promised payment dates were missed",
    "commitment_too_far_out": "the promised payment date was further out than policy allows",
    "payment_claim_unverified": "a claimed payment was never confirmed on our end",
}


def _target_label(target: Optional[str]) -> str:
    if not target:
        return "the recipient"
    if target == "pm":
        return "the PM"
    if target == "customer":
        return "the customer"
    return target.replace("_", " ")


def explain_event(chase: Dict[str, Any], event: Dict[str, Any]) -> Optional[str]:
    """Returns a one-paragraph explanation, or None for event kinds that
    don't represent a decision worth narrating (e.g. 'created')."""
    kind = event.get("kind")
    detail: Dict[str, Any] = event.get("detail") or {}
    invoice_ref = chase.get("invoice_no") or chase.get("case_key") or "this invoice"
    who = _target_label(detail.get("target") or chase.get("target"))

    if kind in ("outreach_sent", "dry_run_send"):
        action_kind = detail.get("kind")
        if action_kind == "outreach":
            return f"The agent reached out to {who} because invoice {invoice_ref} is overdue and no one had been contacted about it yet."
        if action_kind == "nudge":
            return f"The agent followed up with {who} because the previous message went unanswered and the follow-up interval had passed."
        if action_kind == "confirm":
            return f"The agent sent a confirmation to {who} because they gave a specific payment date, which is now being tracked."
        if action_kind == "clarify":
            return f"The agent asked {who} for a specific date because their last reply didn't resolve to a clear commitment ({detail.get('reason', 'no reason given')})."
        if action_kind == "rechase":
            return f"The agent followed up with {who} because a previously promised payment date passed with no payment posted."
        if action_kind == "verify_check":
            return f"The agent followed up with {who} to confirm a claimed payment that hasn't shown up on our end yet."
        if action_kind == "checkback_ack":
            return f"The agent confirmed a check-back with {who}, since they asked to be followed up with later rather than committing to a payment date now."
        if action_kind == "blocker_ack":
            return f"The agent acknowledged a blocker {who} reported and asked when to check back, since the blocker itself had no resolution date."
        if action_kind == "blocker_check_in":
            return f"The agent checked in with {who} because a previously reported blocker's expected resolution date passed with no update."
        if action_kind == "ask_for_customer_email":
            return "The agent asked the PM for the customer's email address, since a redirect to the customer was requested but no address was known."
        return f"The agent sent a message to {who} about invoice {invoice_ref}."

    if kind == "reply_received":
        sentiment = detail.get("sentiment")
        review = detail.get("requires_human_review")
        extra = f" The reply's tone was flagged as {sentiment}." if sentiment in ("frustrated", "angry") else ""
        flag = " This reply was flagged for human review." if review else ""
        return f"{who.capitalize()} replied about invoice {invoice_ref}.{extra}{flag}"

    if kind == "blocker_reported":
        blocker_type = (detail.get("blocker_type") or "unspecified").replace("_", " ")
        resolution = detail.get("blocker_resolution_date")
        if resolution:
            return f"The agent recorded a blocker ({blocker_type}) on invoice {invoice_ref}, expected to resolve around {resolution}, so it scheduled a check-in around then instead of the usual follow-up interval."
        return f"The agent recorded a blocker ({blocker_type}) on invoice {invoice_ref} but no resolution date was given, so it asked when to check back."

    if kind == "checkback_scheduled":
        followup = detail.get("followup_date")
        if followup:
            return f"The selected objective was to get a specific commitment from {who}. Since they asked to be checked back with instead, the agent scheduled the next touch for {followup} rather than treating it as a stall."
        return f"{who.capitalize()} asked to be checked back with later without giving a specific date, so the agent asked when a good time would be."

    if kind == "commitment_tracked":
        return f"{who.capitalize()} gave a specific payment date ({detail.get('promised_date')}) for invoice {invoice_ref}, so the agent stopped regular follow-ups and will check back once that date (plus a grace period) has passed."

    if kind == "commitment_missed":
        return f"The payment date previously promised by {_target_label(detail.get('target'))} for invoice {invoice_ref} passed with no payment posted, so the agent followed up again (missed commitment #{detail.get('missed_count')})."

    if kind == "clarify_requested":
        return f"The agent asked {who} for a specific commitment because the reply was ambiguous: {detail.get('reason', 'no reason given')}."

    if kind in ("handoff_to_customer", "handoff_to_contact"):
        target_desc = "the customer" if kind == "handoff_to_customer" else (detail.get("contact_role") or "an internal contact").replace("_", " ")
        return f"The agent redirected outreach on invoice {invoice_ref} to {target_desc}, as requested in the prior reply."

    if kind == "escalated":
        reason_key = detail.get("reason", "")
        reason_text = _ESCALATION_REASONS.get(reason_key, detail.get("detail") or reason_key or "a policy condition was met")
        return f"The agent escalated invoice {invoice_ref} to a human because {reason_text}."

    if kind == "suppressed":
        return f"The agent stopped all further outreach on invoice {invoice_ref} because {who} explicitly asked to be unsubscribed."

    if kind == "out_of_office_detected":
        return_date = detail.get("return_date")
        if return_date:
            return f"The agent detected an out-of-office autoresponder from {who} and rescheduled the next follow-up for just after their stated return date ({return_date}) instead of treating it as a real reply."
        return f"The agent detected an out-of-office autoresponder from {who} and rescheduled the next follow-up, without treating the lack of a real reply as a stall."

    if kind == "closed":
        if detail.get("reason") == "paid":
            return f"Invoice {invoice_ref} was confirmed paid, so the agent closed the chase and stopped all further outreach."
        return f"Invoice {invoice_ref}'s chase was closed manually{': ' + detail.get('reason') if detail.get('reason') else ''}."

    if kind == "trajectory_assessed":
        verdict = detail.get("verdict")
        return f"The agent's ongoing-conversation check rated this exchange as '{verdict}'{': ' + detail.get('reason') if detail.get('reason') else ''}."

    return None
