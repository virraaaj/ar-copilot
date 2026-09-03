"""Automated-assistant disclosure for customer-bound outreach (2026-09-03).

Business requirement: every outbound email to an EXTERNAL CUSTOMER must
disclose that it came from an automated assistant and offer a route to a
human. Emails to the INTERNAL PM must NOT carry it -- PMs are inside the
organisation and already know an agent is running this. This must be
deterministic (appended in code at send time, never left to an LLM
prompt, which will eventually omit it) and enforced fail-closed
immediately before the actual send.

PM-vs-customer routing intentionally reuses resolve_recipient()'s own
rule (case["target"] == "pm", or tactic in PM_DIRECTED_TACTICS) rather
than inventing a second, competing notion of who a message is "really"
for -- see communication.py for the full history of why that rule
exists.
"""
from __future__ import annotations

from typing import Any, Dict

from app.outcome_agent.loop.communication import PM_DIRECTED_TACTICS

DISCLOSURE_TEXT = (
    "This message was sent by an automated assistant on behalf of the "
    "Accounts Receivable team. If you'd prefer to speak with someone "
    "directly, just reply and a member of the team will pick this up."
)


def is_customer_bound(case: Dict[str, Any], tactic: str) -> bool:
    """True when this send's recipient is the external customer, not the PM.

    Mirrors resolve_recipient()'s branch condition exactly (communication.py)
    so disclosure routing can never disagree with recipient routing.
    """
    return not (case.get("target") == "pm" or tactic in PM_DIRECTED_TACTICS)


def has_disclosure(body: str) -> bool:
    return DISCLOSURE_TEXT in (body or "")


def append_disclosure(body: str) -> str:
    """Append the disclosure to `body`. Idempotent: calling this twice on
    the same (already-disclosed) body does not duplicate the text."""
    if has_disclosure(body):
        return body
    body = body or ""
    sep = "\n\n" if body and not body.endswith("\n") else ("\n" if body else "")
    return f"{body}{sep}{DISCLOSURE_TEXT}"
