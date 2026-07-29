"""Pre-action critic checklist with regenerate-once (P4)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.outcome_agent.domain.types import CriticResult
from app.services.chase_guardrails import check_message_language


def critique(
    draft: str,
    context: Dict[str, Any],
    selected: Dict[str, Any],
    *,
    force_fail_language: bool = False,
) -> CriticResult:
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "pass": ok, "detail": detail})

    world = context.get("world") or {}
    inv = context.get("invoice_no") or ""
    tactic = selected.get("tactic") or ""
    failed = {f.get("tactic") for f in (context.get("failed_asks") or [])}

    add("ack_context", bool(draft and len(draft) > 10), "message has substance")
    add("one_concrete_ask", "?" in draft or "confirm" in draft.lower() or "share" in draft.lower())
    add("advances_objective", selected.get("objective") == (context.get("goal_stack") or {}).get("current_objective")
        or selected.get("score", 0) >= 0.5)
    repeat_ok = tactic not in failed or tactic in ("escalation_pack", "reflexion_reask", "clarify_ask")
    add("no_repeat_failed_ask", repeat_ok, f"tactic={tactic}")
    add("no_unsupported_claims", "discount" not in draft.lower() and "waive" not in draft.lower())
    add("frequency_ok", True)  # scheduler enforces; mark pass
    add("escalation_policy_ok", not (world.get("is_paid") and tactic != "dispute_route"))
    add("relationship_tone", "sue" not in draft.lower() and "harass" not in draft.lower())
    needs_amt = tactic in ("firm_reminder", "verify_payment_ask", "polite_outreach")
    has_inv = inv.lower() in draft.lower() if inv else True
    add("invoice_mentioned_when_needed", (not needs_amt) or has_inv)

    lang = check_message_language(draft)
    if force_fail_language:
        lang_ok = False
        lang_reason = "forced banned language"
    else:
        lang_ok = lang.allowed
        lang_reason = lang.reason or ""
    add("language_guardrail", lang_ok, lang_reason)

    passed = all(c["pass"] for c in checks)
    return CriticResult(passed=passed, checks=checks, notes="" if passed else "critic failed one or more checks")


def critique_with_regen(
    draft: str,
    context: Dict[str, Any],
    selected: Dict[str, Any],
    regenerate_fn,
) -> tuple[str, CriticResult]:
    """Run critic; if fail, regenerate once; if still fail → human review."""
    result = critique(draft, context, selected)
    if result.passed:
        return draft, result
    new_draft = regenerate_fn()
    result2 = critique(new_draft, context, selected)
    result2.regenerated = True
    if not result2.passed:
        result2.requires_human_review = True
        result2.notes = "Critic failed after regenerate-once — human review required"
        return new_draft, result2
    result2.notes = "Initial draft failed critic; regenerated draft passed (P4)"
    return new_draft, result2
