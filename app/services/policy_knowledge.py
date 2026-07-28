"""
Static Knowledge / RAG layer for policy docs (long-horizon outcome agent
spec §6.9) -- a small fixed library of policy documents ("collections
policy", "escalation policy", "tone/communication guidelines", "dispute
handling SOP", "approved email patterns", "contact frequency policy")
that chase_composer.py pulls relevant snippets from when drafting a
message, so the agent's tone/behavior is grounded in written policy
rather than only the hardcoded prompt rules.

Deliberately NOT a vector index -- the spec calls for "simple keyword
retrieval", and six short documents don't need embeddings to search well.
retrieve() just scores documents by word overlap with the query.

Where a document states a concrete number (nudge interval, blackout
dates, banned phrases, ...) it's rendered from the real enforced values
in chase_machine.ChaseConfig / chase_guardrails.py rather than a second,
driftable copy -- same pattern as outcome_definition.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List

from app.services.chase_guardrails import _BANNED_PATTERNS, BLACKOUT_DATES, HIGH_DOLLAR_THRESHOLD

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "or", "in", "on",
    "for", "with", "this", "that", "it", "as", "be", "by", "should", "please", "invoice",
}


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _readable_banned_terms() -> str:
    """Strips regex syntax (\\b, (?:...), etc.) from _BANNED_PATTERNS
    down to a human-readable phrase list -- read from the actual
    guardrail patterns so this document can't list a phrase the
    guardrail doesn't really enforce, or omit one it does."""
    terms = []
    for pattern in _BANNED_PATTERNS:
        raw = pattern.pattern
        cleaned = re.sub(r"\\b|\(\?:|\)|\[.*?\]|\?|\\", "", raw)
        terms.append(cleaned.strip())
    return ", ".join(terms)


@dataclass(frozen=True)
class PolicyDocument:
    id: str
    title: str
    category: str
    text: str


def _build_documents(config: Any) -> List[PolicyDocument]:
    return [
        PolicyDocument(
            id="collections-policy",
            title="Collections Policy",
            category="collections",
            text=(
                "The agent pursues open invoices past their due date by contacting the "
                "project's PM first, then handing off to the customer once the PM confirms "
                "who to reach. A chase closes as paid only after the real backend confirms "
                f"payment. After {config.max_missed_commitments} missed payment commitments, "
                "the chase escalates to a human collector rather than continuing to nudge."
            ),
        ),
        PolicyDocument(
            id="escalation-policy",
            title="Escalation Policy",
            category="escalation",
            text=(
                f"Escalate to a human when: a customer disputes the invoice; a customer "
                f"reports a blocker with no resolution date after repeated check-ins; "
                f"{config.max_missed_commitments} or more payment commitments have been "
                f"missed; or an invoice's open amount is at or above "
                f"${HIGH_DOLLAR_THRESHOLD:,.0f}, which requires human approval before the "
                f"very first automated outreach. Escalated chases are never re-contacted "
                f"automatically -- a human must resume or close them."
            ),
        ),
        PolicyDocument(
            id="tone-guidelines",
            title="Tone and Communication Guidelines",
            category="tone",
            text=(
                "Messages should read as warm, professional, and concise -- never robotic, "
                "never pushy. Never state or imply a payment date that hasn't already been "
                "confirmed. Never make commitments, discounts, extensions, or promises on "
                "the company's behalf. Never mention dollar amounts that weren't already "
                "part of the required message. Keep messages under 60 words."
            ),
        ),
        PolicyDocument(
            id="dispute-handling-sop",
            title="Dispute Handling SOP",
            category="dispute",
            text=(
                "If a customer disputes an invoice (wrong amount, services not delivered, "
                "billing error), the agent does not argue the dispute or continue nudging "
                "for payment. It acknowledges the dispute and escalates immediately to a "
                "human collector who can review the contract and account history. The agent "
                "never offers a discount, waiver, or reduced balance to resolve a dispute."
            ),
        ),
        PolicyDocument(
            id="approved-email-patterns",
            title="Approved Email Patterns",
            category="patterns",
            text=(
                "Every outgoing message must reference the specific invoice by number and "
                "ask a clear next-step question (a payment date, or who to contact). "
                f"The following language is never allowed in any outgoing message, "
                f"template or AI-composed: {_readable_banned_terms()}."
            ),
        ),
        PolicyDocument(
            id="contact-frequency-policy",
            title="Contact Frequency Policy",
            category="frequency",
            text=(
                f"The agent waits at least {config.nudge_interval_days} days between "
                f"follow-ups to the same recipient, and sends at most {config.max_nudges} "
                f"nudges before treating the chase as stalled. Outreach is suppressed "
                f"entirely on configured blackout dates"
                + (f" ({', '.join(BLACKOUT_DATES)})" if BLACKOUT_DATES else " (none currently configured)")
                + "."
            ),
        ),
    ]


def retrieve(query: str, config: Any, top_k: int = 2) -> List[PolicyDocument]:
    """Simple keyword-overlap retrieval (spec §6.9 explicitly calls for
    this, not embeddings) -- scores each document by how many
    non-stopword terms it shares with the query, returns the top_k with
    at least one match. Ties broken by document order (stable sort)."""
    query_words = _words(query)
    if not query_words:
        return []
    documents = _build_documents(config)
    scored = [(len(query_words & _words(doc.title + " " + doc.text)), doc) for doc in documents]
    scored = [(score, doc) for score, doc in scored if score > 0]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [doc for _, doc in scored[:top_k]]


def list_documents(config: Any) -> List[PolicyDocument]:
    return _build_documents(config)
