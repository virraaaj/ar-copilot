"""Pre-action critic checklist with regenerate-once (P4)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.outcome_agent.domain.types import CriticResult
from app.outcome_agent.loop.reply_interpreter import _extract_date
from app.services.chase_guardrails import check_message_language

# Was "?" in draft or "confirm"/"share" in draft.lower() -- a substring
# check unrelated to how many distinct asks are actually in the email, which
# both under- and over-fired (a single ask phrased as a statement failed;
# a genuinely bundled two-part ask that happened to include "confirm" once
# still passed). Replaced 2026-08-05 (found via mass-conversation stress
# testing) with a per-sentence ask-marker count so "one ask" and "more than
# one ask" are two different, correctly-named checks.
_ASK_MARKERS_RE = re.compile(
    r"\?|\bplease\s+(?:confirm|provide|share|send|advise|let (?:us|me) know|reply|respond)\b|"
    r"\bcan you\b|\bcould you\b|\bwould you\b|\bkindly\b|"
    r"\b(?:we|i)(?:'d| would) like to know\b|\blet (?:us|me) know\b",
    re.IGNORECASE,
)
# A single sentence can still bundle two distinct requests ("...your contact
# name and also your preferred contact method?") -- one "?" but two asks.
# Count distinct requested-item nouns within an ask sentence as a proxy for
# how many separate things are actually being asked for.
_ASK_ITEM_RE = re.compile(
    r"\b(payment date|due date|expected date|date|contact name|primary contact|"
    r"contact method|preferred (?:contact )?method|documentation|supporting document|"
    r"documents?|invoice copy|amount|confirmation|phone(?: number)?|"
    r"email(?: address)?|mailing address|address)\b",
    re.IGNORECASE,
)


def _count_asks(draft: str) -> int:
    # Split on newlines too, not just sentence punctuation -- an invoice-
    # details bullet block ("Amount: $45,000.00\nDue date: 2026-07-20") has
    # no periods, so without this a whole greeting+details+ask block became
    # one giant "sentence," and words like "Amount"/"Due date" in the
    # header got counted as extra ask items alongside the real ask (found
    # 2026-08-06, once drafts started including invoice details up top).
    sentences = re.split(r"(?<=[.!?])\s+|\n+", draft or "")
    total = 0
    for s in sentences:
        if not _ASK_MARKERS_RE.search(s):
            continue
        items = {m.group(0).lower() for m in _ASK_ITEM_RE.finditer(s)}
        if items:
            total += len(items)
        elif "?" in s:
            # A genuine question with no named item noun (e.g. "would you
            # like us to reach out?") still counts as one ask.
            total += 1
        # else: the sentence's only marker hit is a generic call-to-action
        # ("please reply on this thread", "let us know") with no "?" and no
        # item -- that's a closing invitation to respond to the ask(s)
        # already made, not a new distinct ask. Was previously counted via
        # max(1, 0)=1, which inflated the count on every polite sign-off and
        # caused real false multi-ask flags (found 2026-08-06).
    return total


# Fix 2 (2026-08-20, FIX_PLAN_commitment_grounding.md, root cause RC2): the
# critic had a guardrail for capability claims ("we'll issue a refund") but
# nothing that checked whether a FACTUAL claim in the draft -- specifically,
# a date attributed to the counterparty as something they promised -- is
# actually on record. That's how the Silverline bug shipped a real email:
# routing put the LLM on a tactic (confirm_promise) whose job is to
# acknowledge an existing commitment, no commitment existed, and the LLM
# improvised one from the invoice's due_date. Fix 1 closes the routing hole
# that caused it; this is the backstop that catches the same *shape* of bug
# even if routing is wrong again in some future way this file's history
# hasn't seen yet -- "prefer a false block over a fabricated assertion."
_COMMITMENT_ATTRIBUTION_RE = re.compile(
    r"\b(expected to pay by|you(?:'ve| have)? committed to (?:pay(?:ing)?|paying)|"
    r"you indicated you would pay|you agreed to pay|you promised to pay|"
    r"i(?:'ve| have) noted that .{0,80}(?:is|are) expected to pay|"
    r"noted that .{0,80}will pay|is expected to pay by|are expected to pay by)\b",
    re.IGNORECASE,
)


def _commitment_assertions(draft: str, now: datetime) -> List[str]:
    """Sentences in `draft` that attribute a payment commitment to the
    counterparty, with the date (if any) asserted alongside it -- reuses
    reply_interpreter._extract_date (2026-08-06's date parser) instead of
    writing a second one, per FIX_PLAN_commitment_grounding.md Fix 2."""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", draft or "")
    out: List[tuple[str, Optional[str]]] = []
    for s in sentences:
        if _COMMITMENT_ATTRIBUTION_RE.search(s):
            out.append((s, _extract_date(s.lower(), now)))
    return out


def _month_day(iso_date: Optional[str]) -> Optional[tuple[int, int]]:
    if not iso_date:
        return None
    parts = iso_date.split("-")
    if len(parts) != 3:
        return None
    try:
        return int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _no_fabricated_commitment(draft: str, active_commitments: List[Dict[str, Any]]) -> tuple[bool, str]:
    """Fail if the draft attributes a payment commitment to the counterparty
    that has no matching active payment_date commitment on record. Matches
    on (month, day) as well as the exact ISO date, since a sentence like
    "expected to pay by August 20th" has no year in it and shouldn't be
    failed just because the wall clock's default year guess differs from
    the commitment's actual year."""
    assertions = _commitment_assertions(draft, datetime.utcnow())
    if not assertions:
        return True, ""
    active_dates = {
        c.get("date") for c in active_commitments if c.get("type") == "payment_date" and c.get("date")
    }
    active_month_days = {_month_day(d) for d in active_dates if _month_day(d)}
    for sentence, asserted_date in assertions:
        matched = asserted_date is not None and (
            asserted_date in active_dates or _month_day(asserted_date) in active_month_days
        )
        if not matched:
            return False, (
                f"draft attributes a payment commitment to the counterparty "
                f"(\"{sentence.strip()}\") with no matching active payment_date "
                f"commitment on record"
            )
    return True, ""


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
    ask_count = _count_asks(draft)
    add("one_concrete_ask", ask_count >= 1, f"{ask_count} ask(s) detected")
    add("no_multiple_asks", ask_count <= 1, f"{ask_count} ask(s) detected -- should be exactly one")
    add("advances_objective", selected.get("objective") == (context.get("goal_stack") or {}).get("current_objective")
        or selected.get("score", 0) >= 0.5)
    repeat_ok = tactic not in failed or tactic in ("escalation_pack", "reflexion_reask", "clarify_ask")
    add("no_repeat_failed_ask", repeat_ok, f"tactic={tactic}")
    add("no_unsupported_claims", "discount" not in draft.lower() and "waive" not in draft.lower())
    fab_ok, fab_reason = _no_fabricated_commitment(draft, context.get("active_commitments") or [])
    add("no_fabricated_commitment", fab_ok, fab_reason)
    # Added 2026-08-04: the mailbox has no file-attachment capability
    # anywhere in this system (MailboxStore.send and GraphEmailSender.send
    # both take a plain text/HTML body only) -- a draft that says "please
    # find attached" or offers to attach documents makes a promise the
    # agent can never keep, since nothing downstream can act on it.
    low = draft.lower()
    add(
        "no_attachment_promise",
        "attach" not in low,
        "draft references an attachment, but this system cannot attach files",
    )
    # Added 2026-08-05 (user feedback after mass-conversation stress
    # testing): this system's entire capability is "send this plain-text
    # email, read a reply in the same thread." It cannot attach files, place
    # or receive phone calls, book meetings, hand off to a live person, or
    # take any action on the invoice itself (refund/credit/payment plan).
    # A draft promising any of that is a promise nothing downstream can
    # fulfill -- same failure mode as no_attachment_promise above, just not
    # limited to attachments. This regex is necessarily a non-exhaustive
    # sample of phrasing; the primary control is the llm_draft system
    # prompt instruction not to promise anything outside its real
    # capabilities -- this check is the deterministic backstop.
    _unsupported_promise_re = re.search(
        r"\b(a (?:representative|colleague|team member|person) will|"
        r"i(?:'ll| will) have (?:someone|a colleague|a rep)|"
        r"(?:someone|a rep|a representative) will (?:reach out|contact|call|follow up)|"
        r"let me (?:get|have) (?:a colleague|someone) (?:to )?reach out|"
        r"share (?:the |a )?(?:invoice )?pdf|"
        r"send (?:you |over )?(?:the |a )?(?:invoice )?(?:pdf|copy)|"
        r"(?:give|call) you a call|schedule a (?:call|meeting)|"
        r"set up a (?:call|meeting|payment plan)|(?:someone|i) (?:will|'ll) call|"
        r"issue (?:a |an )?(?:refund|credit)|payment (?:portal|link)|"
        r"send (?:you |over )?a link|"
        # Added 2026-08-18 (found via guided-demo review): a draft can
        # offer to fulfil a request without using any of the direct verbs
        # above -- "if you need a copy of the invoice ... we'll handle it
        # from here" promises document delivery just as concretely as
        # "I'll send you a copy", but neither half alone matches ("need a
        # copy" isn't a promise; "we'll handle it" isn't specific) and the
        # deterministic check missed it entirely. This system can only
        # send/receive plain-text email in the same thread -- it cannot
        # actually "handle" a document, arrangement, or request open-endedly.
        r"we('ll| will) handle (?:it|that|this) from here|"
        r"if you need .{0,60}(?:copy|documentation|attachment)|"
        r"want to discuss arrangements)\b",
        low,
    )
    add(
        "no_unsupported_promise",
        _unsupported_promise_re is None,
        "draft promises a capability (call, meeting, human handoff, document, "
        "refund/credit, portal link) this system cannot actually deliver",
    )
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
