"""
Evaluator / Critic (long-horizon outcome agent spec §6.14) -- checks a
composed message before it sends. Deliberately deterministic-first: every
check here is a plain rule over the text/chase, no LLM call required, so
it always runs (even with no model configured, matching §4's "deterministic
mock AI adapters" allowance) and is fast enough to run on every send, not
just occasionally.

Only ever evaluates AI-COMPOSED text, never the deterministic template --
the template is trusted by construction (it's our own fixed copy), so
there's nothing to critique there. chase_composer.compose_and_evaluate is
what wires this in with the spec's "regenerate once, then require human
review" policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from app.services.chase_guardrails import check_message_language


@dataclass(frozen=True)
class EvaluationResult:
    passed: bool
    checklist: Dict[str, bool] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)


def evaluate_message(text: str, chase: Dict[str, Any]) -> EvaluationResult:
    checklist: Dict[str, bool] = {}
    failures: List[str] = []

    # Avoids threatening/discount language -- reuses the exact same
    # banned-phrase check that gates the send itself (chase_guardrails),
    # so there's one list to keep accurate, not two that can drift.
    language = check_message_language(text)
    checklist["avoids_threatening_or_discount_language"] = language.allowed
    if not language.allowed:
        failures.append(language.reason or "banned language detected")

    # Asks for one concrete next step -- a plain question mark is the
    # cheap, reliable proxy every real template in this engine already
    # satisfies (every kind ends in a question by construction).
    asks_for_next_step = "?" in text
    checklist["asks_for_one_concrete_next_step"] = asks_for_next_step
    if not asks_for_next_step:
        failures.append("message doesn't ask a question / request a concrete next step")

    # Contains invoice reference when we have one to reference.
    invoice_ref = chase.get("invoice_no") or chase.get("case_key")
    has_invoice_ref = bool(invoice_ref) and str(invoice_ref) in text
    checklist["contains_invoice_reference"] = has_invoice_ref
    if not has_invoice_ref:
        failures.append(f"message doesn't mention invoice {invoice_ref}")

    # Not degenerately short -- the cheapest first gate against a
    # malformed/truncated generation.
    long_enough = len(text.strip()) >= 15
    checklist["not_degenerately_short"] = long_enough
    if not long_enough:
        failures.append("message is too short to be a real follow-up")

    # Preserves customer relationship -- no shouting. Purely-alphabetic
    # check (no digits) so an invoice/reference number like "INV-4821"
    # never false-positives as shouting.
    shouting = any(w.isalpha() and w.isupper() and len(w) > 3 for w in text.split())
    checklist["preserves_relationship_tone"] = not shouting
    if shouting:
        failures.append("message contains all-caps shouting")

    return EvaluationResult(passed=all(checklist.values()), checklist=checklist, failures=failures)
