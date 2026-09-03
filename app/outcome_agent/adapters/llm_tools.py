"""Azure OpenAI forced-tool adapters for interpret / plan / draft / judge."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.outcome_agent.loop.reply_interpreter import (
    _DISPUTE_RE,
    DeterministicReplyInterpreter,
    InterpretedReply,
)

logger = logging.getLogger(__name__)

# Deterministic checks whose failure hard-overrides the LLM judge's own
# "passed" verdict below, regardless of what the LLM concluded. Kept
# deliberately narrow: these are the checks where being wrong is genuinely
# unsafe (a threat, a promise the system can't keep, banned language) --
# not the more subjective content-quality checks (multi-ask, one-concrete-
# ask, advances-objective, ...) that the judge prompt explicitly asks the
# LLM to decide independently ("do not just copy the precheck's failures
# through"). Before this list existed, ANY deterministic failure hard-
# overrode the LLM either way, silently contradicting that instruction --
# the regex-based ask-counter is a known-blunt heuristic (see critic.py's
# own comments on it being tuned against false positives more than once),
# so a single mis-flagged "multi_ask" could force a real block even when
# the LLM had already correctly judged the draft fine. Found 2026-08-18
# reviewing the guided demo: a clean, single-topic follow-up ("give us an
# update, and confirm the date is still Aug 20") was blocked outright this
# way, then never actually regenerated into anything different since the
# regex flags the same pattern either way.
_SAFETY_CRITICAL_CHECKS = {
    "language_guardrail",
    "no_unsupported_promise",
    "no_unsupported_claims",
    "no_attachment_promise",
    "relationship_tone",
    "escalation_policy_ok",
    # Added 2026-08-20 (FIX_PLAN_commitment_grounding.md, Fix 2): a draft
    # asserting a payment date the counterparty never gave is a factual
    # fabrication, not a content-quality judgment call -- same category of
    # "genuinely unsafe to be wrong about" as the checks above. The LLM
    # judge must not be able to wave this one through the way it can with
    # multi-ask or advances-objective.
    "no_fabricated_commitment",
}


def _parse_tool(message: Any, tool_name: str) -> Tuple[Dict[str, Any], Optional[str]]:
    calls = getattr(message, "tool_calls", None) or []
    if not calls:
        return {}, f"model did not call {tool_name}"
    fn = calls[0].function
    if fn.name != tool_name:
        return {}, f"expected tool {tool_name}, got {fn.name}"
    try:
        return json.loads(fn.arguments or "{}"), None
    except json.JSONDecodeError as exc:
        return {}, f"invalid tool JSON: {exc}"


def _trace_call(
    *,
    name: str,
    mode: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Any = None,
    args: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    tokens: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Structured LLM trace: request vs response vs token split."""
    prompt = int(getattr(tokens, "prompt", 0) or 0) if tokens is not None else 0
    completion = int(getattr(tokens, "completion", 0) or 0) if tokens is not None else 0
    if tokens is not None and prompt == 0 and completion == 0:
        # plain int total only
        try:
            total = int(tokens)
        except Exception:
            total = 0
    else:
        total = int(tokens) if tokens is not None else prompt + completion
    out: Dict[str, Any] = {
        "kind": "llm",
        "name": name,
        "mode": mode,
        "request": {
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        },
        "response": {
            "tool_args": args,
            "result": result if result is not None else args,
            "error": error,
        },
        "tokens": {
            "input": prompt,
            "output": completion,
            "total": total,
        },
        # flat aliases (older UI / tests)
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "messages": messages,
        "args": args,
        "result": result if result is not None else args,
        "error": error,
    }
    if extra:
        out.update(extra)
    return out


class ScriptedLLM:
    """Test double: returns canned tool_calls in order."""

    def __init__(self, scripts: List[Dict[str, Any]]) -> None:
        self._scripts = list(scripts)
        self.calls: List[Dict[str, Any]] = []

    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if not self._scripts:
            raise RuntimeError("ScriptedLLM: no more scripts")
        script = self._scripts.pop(0)
        name = script["name"]
        args = script.get("arguments") or {}

        class Fn:
            pass

        class Tc:
            pass

        class Msg:
            pass

        fn = Fn()
        fn.name = name
        fn.arguments = json.dumps(args)
        tc = Tc()
        tc.function = fn
        msg = Msg()
        msg.tool_calls = [tc]
        msg.content = None
        if return_usage:
            from app.services.azure_openai import TokenUsage

            return msg, TokenUsage(10, 6, 4)
        return msg


async def llm_interpret(
    llm: Any,
    text: str,
    context: Dict[str, Any],
    *,
    mock: bool = False,
    now: Optional[datetime] = None,
) -> Tuple[InterpretedReply, Dict[str, Any]]:
    """Returns (interpretation, call_trace).

    `now` is the agent's current clock (the simulation clock in demo/test
    runs, real wall-clock in production) -- passed through to the prompt
    as `today` so the model can turn a relative reference like "in 3 days"
    or "next Friday" into the correct absolute ISO date. Added 2026-08-06
    (user feedback): without a "today" anchor in the prompt, the model had
    nothing to compute a relative date against and previously left
    promised_date/followup_date empty for these replies.
    """
    now = now or datetime.utcnow()
    tool_name = "record_reply_interpretation"
    schema = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "Record structured interpretation of a customer email reply.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reply_type": {
                        "type": "string",
                        "enum": [
                            "payment_promise",
                            "follow_up_commitment",
                            "approval_blocker",
                            "cash_flow_blocker",
                            "missing_invoice",
                            "dispute",
                            "already_paid",
                            "vague_delay",
                            "unsubscribe",
                            "wrong_contact",
                            "unknown",
                        ],
                    },
                    "confidence": {"type": "number"},
                    "promised_date": {"type": "string", "description": "ISO date or empty"},
                    "followup_date": {"type": "string"},
                    "blocker_type": {"type": "string"},
                    "blocker_description": {"type": "string"},
                    "sentiment": {"type": "string"},
                    "needs_clarification": {"type": "boolean"},
                    "summary": {"type": "string"},
                    "mentioned_contact_email": {
                        "type": "string",
                        "description": "Only if the customer explicitly gave a different email to use going forward.",
                    },
                    "mentioned_contact_name": {
                        "type": "string",
                        "description": "Only if the customer explicitly stated their own name.",
                    },
                    "special_instruction": {
                        "type": "string",
                        "description": (
                            "Any explicit meta-request about how to communicate with them "
                            "(tone, care, formality) that isn't captured by the other fields -- "
                            "empty if none."
                        ),
                    },
                    "authorizes_customer_contact": {
                        "type": "boolean",
                        "description": (
                            "True ONLY if the PM explicitly says to go ahead and contact the "
                            "customer directly (in reply to a pm_awareness_check email). "
                            "'Let me check and get back to you' is NOT authorization -- that's "
                            "a checkback, false. Only an explicit go-ahead is true."
                        ),
                    },
                },
                "required": ["reply_type", "confidence", "summary"],
            },
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You interpret AR collections email replies. "
                f"Call {tool_name} exactly once. Prefer concrete dates (YYYY-MM-DD). "
                "The user message includes \"today\" -- the current date. Any relative time "
                "reference in the reply (\"in 3 days\", \"give me a week\", \"early next week\", "
                "\"by Friday\", \"end of month\") MUST be converted to an absolute YYYY-MM-DD "
                "date computed from that today value and placed in promised_date (if it's a "
                "payment commitment) or followup_date (if it's just a checkback/callback date) "
                "-- never leave the date field empty just because the reply phrased it "
                "relatively instead of naming a calendar date. "
                "A blocker is when the reply says payment itself is gated on something -- "
                "an approval, a PO or budget sign-off, a legal/contract review, a dispute "
                "over the service or amount -- classify these as approval_blocker or "
                "cash_flow_blocker even if the customer doesn't use the word 'blocker'. The "
                "test is what happens to PAYMENT: does the reply say payment can't happen "
                "until something clears? If yes, it's a blocker -- this is true EVEN IF the "
                "reply also uses words like 'check' or 'verify' to describe that gate. "
                "Example: 'we need to check a few things with our team before we can "
                "process this payment' IS a blocker -- 'before we can process this payment' "
                "explicitly ties the check to payment being gated. Contrast with 'let me "
                "check with my team and I'll confirm a payment date' -- here nothing says "
                "payment is gated, the customer is just not ready to name a date yet; that's "
                "a normal follow_up_commitment/vague_delay, not a blocker. Do not let the "
                "presence of the word 'check' by itself decide this -- always look for "
                "whether payment is explicitly described as waiting on something. "
                "authorizes_customer_contact defaults to false and should almost always stay "
                "false -- 'let me check and get back to you', 'I'll look into it', or silence "
                "on the question are NOT authorization, they're the PM saying they need more "
                "time. Only set it true if the PM clearly says to go ahead and contact the "
                "customer (or gives a distinct customer contact to use)."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "today": now.date().isoformat(),
                    "reply_text": text,
                    "invoice": (context.get("case") or {}).get("invoice_no"),
                    "world": context.get("world"),
                }
            ),
        },
    ]
    tool_choice = {"type": "function", "function": {"name": tool_name}}
    if mock or llm is None:
        det = DeterministicReplyInterpreter().interpret(text, now=now)
        call = _trace_call(
            name=tool_name,
            mode="mock_deterministic",
            messages=messages,
            tools=[schema],
            tool_choice=tool_choice,
            args=det.__dict__,
            result=det.__dict__,
        )
        return det, call

    message, tokens = await llm.chat(
        messages,
        tools=[schema],
        tool_choice=tool_choice,
        return_usage=True,
    )
    args, err = _parse_tool(message, tool_name)
    if err:
        det = DeterministicReplyInterpreter().interpret(text, now=now)
        call = _trace_call(
            name=tool_name,
            mode="live",
            messages=messages,
            tools=[schema],
            tool_choice=tool_choice,
            args=args,
            result=det.__dict__,
            error=err,
            tokens=tokens,
            extra={"fallback": "deterministic"},
        )
        return det, call
    interp = InterpretedReply(
        reply_type=args.get("reply_type") or "unknown",
        confidence=float(args.get("confidence") or 0.5),
        promised_date=args.get("promised_date") or None,
        followup_date=args.get("followup_date") or None,
        blocker_type=args.get("blocker_type") or None,
        blocker_description=args.get("blocker_description") or None,
        sentiment=args.get("sentiment") or "neutral",
        needs_clarification=bool(args.get("needs_clarification")),
        summary=args.get("summary") or "",
        mentioned_contact_email=args.get("mentioned_contact_email") or None,
        mentioned_contact_name=args.get("mentioned_contact_name") or None,
        special_instruction=args.get("special_instruction") or None,
        authorizes_customer_contact=bool(args.get("authorizes_customer_contact")),
    )
    call = _trace_call(
        name=tool_name,
        mode="live",
        messages=messages,
        tools=[schema],
        tool_choice=tool_choice,
        args=args,
        result=interp.__dict__,
        tokens=tokens,
    )
    return interp, call


# LLM taxonomy -> domain ReplyType used by signal_ingestion.apply_reply_signal.
# Shared by every caller of llm_interpret so the mapping only lives in one
# place (previously duplicated inline in traced_loop.py and scheduler.py).
_REPLY_TYPE_REMAP = {
    "payment_promise": "payment_date",
    "follow_up_commitment": "checkback",
    "approval_blocker": "blocker",
    "cash_flow_blocker": "blocker",
    "already_paid": "paid_claim",
    "vague_delay": "vague",
}
_KNOWN_REPLY_TYPES = (
    "payment_date", "blocker", "checkback", "dispute", "paid_claim",
    "vague", "unsubscribe", "hostile", "handoff", "unknown",
)


async def interpret_reply_llm(
    llm: Any, text: str, context: Dict[str, Any], *, mock: bool = False, now: Optional[datetime] = None
) -> Tuple[InterpretedReply, Dict[str, Any]]:
    """llm_interpret + the LLM-taxonomy remap, in one call. Use this
    instead of llm_interpret directly whenever the result feeds
    apply_reply_signal, which only knows the domain ReplyType values."""
    interp, call = await llm_interpret(llm, text, context, mock=mock, now=now)
    rt = interp.reply_type
    interp.reply_type = _REPLY_TYPE_REMAP.get(rt, rt if rt in _KNOWN_REPLY_TYPES else "unknown")
    # Fix 3 (2026-08-20, FIX_PLAN_commitment_grounding.md, root cause RC3):
    # explicit dispute language always overrides the LLM's own label here,
    # same philosophy as _SAFETY_CRITICAL_CHECKS above -- for a signal this
    # safety-critical (continuing to dun a customer who has formally
    # disputed is a real relationship and compliance risk), the
    # deterministic check is authoritative, not just a hint. The mock/
    # fallback paths already run DeterministicReplyInterpreter directly and
    # get this right; this override is for the live-LLM path, where the
    # model underperformed the regex on the most explicit dispute phrasing
    # possible ("we're disputing this invoice...", classified `blocker` at
    # 90% confidence).
    if _DISPUTE_RE.search((text or "").lower()) and interp.reply_type != "dispute":
        interp.reply_type = "dispute"
        interp.confidence = max(interp.confidence, 0.9)
    return interp, call


async def llm_plan(
    llm: Any,
    context: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    *,
    mock: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    tool_name = "record_next_action_plan"
    schema = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "Choose the next collections action from candidates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selected_tactic": {"type": "string"},
                    "objective": {"type": "string"},
                    "rationale": {"type": "string"},
                    "candidates_considered": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["selected_tactic", "objective", "rationale"],
            },
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You plan the next AR follow-up. Pick one tactic from candidates. "
                "If this case has no dialogue history yet and goal_stack.selected_tactic "
                "is pm_awareness_check, you must pick pm_awareness_check -- the very first "
                "outreach on any case always goes to the PM to ask whether they already "
                "know a payment date, before the customer is ever contacted directly. Only "
                "move to a customer-facing tactic (polite_outreach, soft_nudge, etc.) once "
                "that PM check has actually happened. "
                f"Call {tool_name} exactly once. Do not invent payment status."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "goal_stack": context.get("goal_stack"),
                    "uncertainty": context.get("uncertainty"),
                    "budgets": context.get("budgets"),
                    "candidates": candidates,
                    "active_commitments": context.get("active_commitments"),
                    "active_blockers": context.get("active_blockers"),
                    "is_first_contact": not bool((context.get("dialogue") or {}).get("latest_outbound")),
                }
            ),
        },
    ]
    default = candidates[0] if candidates else {"tactic": "soft_nudge", "objective": "obtain_payment_date"}
    tool_choice = {"type": "function", "function": {"name": tool_name}}
    if mock or llm is None:
        plan = {
            "selected_tactic": default.get("tactic"),
            "objective": default.get("objective"),
            "rationale": "Mock planner selected top-scoring candidate",
            "candidates_considered": [c.get("tactic") for c in candidates],
        }
        return plan, _trace_call(
            name=tool_name, mode="mock", messages=messages, tools=[schema], tool_choice=tool_choice, args=plan, result=plan
        )

    message, tokens = await llm.chat(
        messages,
        tools=[schema],
        tool_choice=tool_choice,
        return_usage=True,
    )
    args, err = _parse_tool(message, tool_name)
    if err:
        plan = {
            "selected_tactic": default.get("tactic"),
            "objective": default.get("objective"),
            "rationale": f"Fallback after LLM error: {err}",
            "candidates_considered": [c.get("tactic") for c in candidates],
        }
        return plan, _trace_call(
            name=tool_name,
            mode="live",
            messages=messages,
            tools=[schema],
            tool_choice=tool_choice,
            args=args,
            result=plan,
            error=err,
            tokens=tokens,
            extra={"fallback": True},
        )
    return args, _trace_call(
        name=tool_name,
        mode="live",
        messages=messages,
        tools=[schema],
        tool_choice=tool_choice,
        args=args,
        result=args,
        tokens=tokens,
    )


async def llm_draft(
    llm: Any,
    case: Dict[str, Any],
    plan: Dict[str, Any],
    context: Dict[str, Any],
    *,
    mock: bool = False,
    force_text: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Returns subject, body, call_trace."""
    from app.outcome_agent.loop.communication import DEFAULT_GENERATOR, PM_DIRECTED_TACTICS

    tool_name = "record_email_draft"
    inv = case.get("invoice_no") or "invoice"
    subject = f"[{case.get('subject_token')}] Invoice {inv}"
    # Who this email actually goes to. Previously only threaded through
    # for tactic=="pm_awareness_check" -- every other PM-directed tactic
    # (firm_reminder, confirm_promise, reflexion_reask, ...) got no signal
    # at all about the recipient, so once a conversation moved past the
    # first message the model had no way to know it was still writing to
    # the PM and defaulted to addressing the customer directly ("Hello
    # Harborview Logistics,") even when the email was correctly routed to
    # the PM's inbox. Found 2026-08-18 via user review: the address was
    # right, the content was still wrong. selected_tactic (not just
    # case.target) is checked too, matching resolve_recipient()'s own
    # rule that pm_awareness_check always reaches the PM.
    recipient_role = (
        "pm"
        if case.get("target") == "pm" or plan.get("selected_tactic") in PM_DIRECTED_TACTICS
        else "customer"
    )
    schema = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "Draft a professional collections follow-up email.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                },
                "required": ["subject", "body"],
            },
        },
    }
    special_instructions = (context.get("dialogue") or {}).get("special_instructions") or []
    messages = [
        {
            "role": "system",
            "content": (
                "The user message's recipient_role tells you who this email actually goes to "
                "-- read it before writing anything, it applies regardless of tactic, not just "
                "pm_awareness_check. If recipient_role is \"pm\": you are writing to an internal "
                "colleague (the Project Manager) about their customer relationship, not to the "
                "customer. Refer to the customer by name in the THIRD PERSON (e.g. \"Harborview "
                "hasn't confirmed a payment date yet\", \"following up on where things stand with "
                "Harborview\") -- never address the customer directly (never open with \"Hello "
                "Harborview Logistics,\" or write as if the customer is reading this) and never "
                "phrase the ask as if the PM personally owes the money. Open with a neutral "
                "greeting like \"Hi,\" or \"Hi team,\", not the customer's name. If recipient_role "
                "is \"customer\": write directly to them as the account holder, addressing them "
                "by name is appropriate. "
                "Write a warm, professional, non-threatening AR email -- this is a business "
                "relationship the company wants to preserve, not a form letter. That does NOT "
                "mean saying so explicitly every time: avoid generic relationship-language "
                "boilerplate like 'we appreciate your partnership', 'we value our partnership', "
                "or 'thank you for your continued partnership' as a closing line -- warmth "
                "should come through in how the ask is phrased, not a stock phrase tacked on "
                "at the end. A plain 'Thank you' or a sign-off with no extra line is often "
                "enough; only add a specific, situational line (e.g. actually thanking them "
                "for a specific update they gave) if it's genuinely earned by what they said. "
                "Always include the actual invoice details (invoice number and amount, and due "
                "date/project if known) as context before asking anything, so the reader "
                "isn't guessing what this is about. "
                "Ask for exactly ONE concrete thing -- never bundle a second, separate "
                "request in the same email (e.g. don't ask for a contact name AND a "
                "preferred contact method; don't ask for a payment date AND supporting "
                "documents). This does NOT mean being curt: 'could you give us an update "
                "on where things stand, including an expected payment date' is still ONE "
                "ask (a status update that naturally includes a date), and is the normal "
                "way to phrase the ask for a customer-facing follow-up -- prefer that "
                "framing over a bare 'give us the exact date' demand. Never write anything "
                "that reads as curt or dismissive like 'that's all we need' or 'for now, "
                "the date is all we need' -- if a second detail would genuinely help later, "
                "it's fine to simply not ask for it yet, without calling attention to the "
                "omission. If more than one piece of information is truly needed, ask only "
                "for the single most important one now and say the rest can follow in a "
                "later reply -- warmly, not as a rule being cited. "
                "If tactic is pm_awareness_check: this email goes to the PM/internal owner, "
                "not the customer. Ask, in ONE single sentence joined with 'or' (not two "
                "separate sentences, not two question marks), whether they're already aware "
                "of an expected payment date for this invoice, or whether the agent should "
                "go ahead and reach out to the customer directly for an update -- e.g. exactly "
                "this shape: 'Are you already aware of an expected payment date for this "
                "invoice, or should we go ahead and reach out to the customer directly for "
                "an update?' That single either/or sentence IS the one ask -- do not add a "
                "second question afterward (like a separate 'please let us know' question), "
                "and do not ask the PM for a payment date as if they were the customer. "
                "If objective is recover_missed_promise and recipient_role is \"pm\": say "
                "plainly that the date the PM previously relayed was missed (e.g. 'The August "
                "20th date didn't come through' -- name the actual date from latest_inbound "
                "or context, don't just say \"the promise\" vaguely), then ask the same shape "
                "of either/or question pm_awareness_check uses, updated for this being a "
                "second round: is there a new date to track, or should we go ahead and reach "
                "out to the customer directly? e.g.: 'The August 20th date didn't come "
                "through -- is there a new date we should track, or should we reach out to "
                "the customer directly?' That single either/or sentence is the one ask, same "
                "rule as pm_awareness_check -- do not also ask a separate generic 'what's the "
                "status' question alongside it. "
                "If tactic is confirm_promise: a date is already on record (see latest_inbound "
                "or context) and hasn't been missed yet -- this is an ACKNOWLEDGMENT, not a "
                "re-ask. Do NOT use the pm_awareness_check either/or pattern here ('are you "
                "already aware of...') -- that question was already answered. Simply confirm "
                "the date you're now tracking (e.g. 'Thanks for the update -- I've noted that "
                "Harborview is expected to pay by August 20th, and I'll follow up if anything "
                "changes.'). If recipient_role is \"pm\", phrase it as noting what THEY told "
                "you, not asking them to confirm it again. "
                "GROUNDING (added 2026-08-20 -- see active_commitments in the user message): "
                "you may state a date as something the counterparty promised to pay ONLY if "
                "that exact date appears in active_commitments with type payment_date. Never "
                "present the invoice's due_date as if the counterparty promised or committed "
                "to it -- due_date is this system's own record of when payment was originally "
                "owed, not something they said. If you have no active payment_date commitment "
                "to acknowledge, do not invent one -- ask for a date instead. "
                f"Call {tool_name} exactly once. Never threaten legal action or offer discounts. "
                "Sign off as 'Accounts Receivable' with no bracketed placeholder text anywhere "
                "in the email (never write things like [Your Name], [Company Name], or "
                "[phone/email] -- if a detail isn't available, omit it rather than leaving a "
                "placeholder). If special_instructions are present, follow them exactly -- "
                "they are explicit requests the customer already made about how to be "
                "contacted (tone, name to use, preferred address). "
                "This system's entire real capability is: send this one email, and read "
                "whatever the customer replies with in the same thread. That is ALL it can "
                "do -- never promise anything outside that, no matter how minor it seems. "
                "In particular (not an exhaustive list -- if you're unsure whether something "
                "is possible, assume it isn't and don't promise it): it cannot attach files "
                "of any kind (never say 'please find attached', never use the word 'attach' "
                "or 'attachment' in any form); no human will personally call, email, or "
                "otherwise follow up outside this thread (never say 'a representative will "
                "contact you', 'I'll have someone follow up', 'let me get a colleague to "
                "reach out', or offer to schedule a call/meeting); and it cannot take any "
                "action on the invoice itself -- no refunds, credits, discounts, payment "
                "plans, or portal/payment links. Do not proactively offer to handle a "
                "document, call, or anything else this system can't deliver -- if the topic "
                "hasn't come up, don't invite it. If a prior reply already asked for one of "
                "those (see latest_inbound), acknowledge briefly (\"noted\" / \"got it\") "
                "without saying what will happen next or by when -- do not write \"we'll "
                "handle it\", \"handled from there\", or any other phrase that promises "
                "resolution, since nothing downstream actually does that. Stay focused on "
                "the one concrete ask instead."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "recipient_role": recipient_role,
                    "invoice": inv,
                    "amount": case.get("amount"),
                    "customer": case.get("customer_name"),
                    "due_date": (case.get("world") or {}).get("due_date"),
                    "project_number": case.get("project_number") or (case.get("world") or {}).get("project_number"),
                    "objective": plan.get("objective"),
                    "tactic": plan.get("selected_tactic"),
                    "latest_inbound": (context.get("dialogue") or {}).get("latest_inbound"),
                    "special_instructions": special_instructions,
                    "policy_snippets": context.get("relevant_policy")
                    or context.get("policy_snippets")
                    or [],
                    # Added 2026-08-20 (FIX_PLAN_commitment_grounding.md, Fix
                    # 2): the ground-truth list the system prompt's GROUNDING
                    # paragraph refers to -- was previously assembled into
                    # the planner's context (llm_plan) but never actually
                    # sent to the drafting call, so the model had no way to
                    # check a date against it even if it wanted to.
                    "active_commitments": context.get("active_commitments") or [],
                }
            ),
        },
    ]
    tool_choice = {"type": "function", "function": {"name": tool_name}}
    if force_text:
        result = {"subject": subject, "body": force_text}
        return subject, force_text, _trace_call(
            name=tool_name, mode="forced", messages=messages, tools=[schema], tool_choice=tool_choice, args=result, result=result
        )

    if mock or llm is None:
        body = DEFAULT_GENERATOR.generate(
            case, plan.get("selected_tactic") or "soft_nudge", plan.get("objective") or "obtain_payment_date"
        )
        result = {"subject": subject, "body": body}
        return subject, body, _trace_call(
            name=tool_name, mode="mock", messages=messages, tools=[schema], tool_choice=tool_choice, args=result, result=result
        )

    message, tokens = await llm.chat(
        messages,
        tools=[schema],
        tool_choice=tool_choice,
        return_usage=True,
    )
    args, err = _parse_tool(message, tool_name)
    if err or not args.get("body"):
        body = DEFAULT_GENERATOR.generate(
            case, plan.get("selected_tactic") or "soft_nudge", plan.get("objective") or "obtain_payment_date"
        )
        result = {"subject": subject, "body": body}
        return subject, body, _trace_call(
            name=tool_name,
            mode="live",
            messages=messages,
            tools=[schema],
            tool_choice=tool_choice,
            args=args,
            result=result,
            error=err or "empty body",
            tokens=tokens,
            extra={"fallback": True},
        )
    return (
        args.get("subject") or subject,
        args["body"],
        _trace_call(
            name=tool_name,
            mode="live",
            messages=messages,
            tools=[schema],
            tool_choice=tool_choice,
            args=args,
            result=args,
            tokens=tokens,
        ),
    )


async def llm_judge(
    llm: Any,
    subject: str,
    body: str,
    case: Dict[str, Any],
    context: Dict[str, Any],
    *,
    mock: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    from app.outcome_agent.loop.critic import critique

    tool_name = "record_email_judgment"
    # Always run deterministic critic; LLM can add narrative when live
    det = critique(
        body,
        context,
        {
            "draft_text": body,
            "tactic": (context.get("goal_stack") or {}).get("selected_tactic") or "soft_nudge",
            "objective": (context.get("goal_stack") or {}).get("current_objective"),
            "score": 0.8,
        },
    )
    det_dict = det.to_dict()
    failures = [c["name"] for c in det.checks if not c.get("pass")]
    schema = {
        "type": "function",
        "function": {
            "name": tool_name,
            "description": "Judge whether the draft email is safe and useful to send.",
            "parameters": {
                "type": "object",
                "properties": {
                    "passed": {"type": "boolean"},
                    "failures": {"type": "array", "items": {"type": "string"}},
                    "checklist": {"type": "object"},
                    "regenerate": {"type": "boolean"},
                    "notes": {"type": "string"},
                },
                "required": ["passed", "failures", "regenerate"],
            },
        },
    }
    messages = [
        {
            "role": "system",
            "content": (
                "Judge AR email drafts. Fail if threatening, multi-ask (more than one distinct "
                "request), or missing invoice ref. A single either/or sentence presenting one "
                "decision ('are you aware of X, or should we do Y instead?') is ONE ask, not "
                "two, even though it has the grammatical shape of two questions -- the "
                "recipient is making one choice, not answering two unrelated questions. "
                "A closing line like 'please reply on this thread' or 'let us know' is not a "
                "second ask either -- it's just inviting a response to the ask already made. "
                "The user message includes a deterministic_precheck -- treat it as a hint, not "
                "an authority: it uses crude keyword heuristics and is sometimes wrong (e.g. it "
                "can flag one_concrete_ask on a draft that has exactly one clear ask just "
                "because that ask isn't phrased as a question). Read the actual draft yourself "
                "and only put a check name in your own `failures` list if you independently "
                "agree, from the text, that it's a real problem -- do not just copy the "
                "precheck's failures through. "
                f"Call {tool_name} exactly once."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "subject": subject,
                    "body": body,
                    "invoice": case.get("invoice_no"),
                    "deterministic_precheck": det_dict,
                }
            ),
        },
    ]
    tool_choice = {"type": "function", "function": {"name": tool_name}}
    if mock or llm is None:
        result = {
            "passed": det.passed,
            "failures": failures,
            "checklist": {c["name"]: c["pass"] for c in det.checks},
            "regenerate": not det.passed,
            "notes": det.notes or "Deterministic critic",
        }
        return result, _trace_call(
            name=tool_name,
            mode="mock",
            messages=messages,
            tools=[schema],
            tool_choice=tool_choice,
            args=result,
            result=result,
            extra={"deterministic_precheck": det_dict},
        )

    message, tokens = await llm.chat(
        messages,
        tools=[schema],
        tool_choice=tool_choice,
        return_usage=True,
    )
    args, err = _parse_tool(message, tool_name)
    if err:
        result = {
            "passed": det.passed,
            "failures": failures,
            "checklist": {c["name"]: c["pass"] for c in det.checks},
            "regenerate": not det.passed,
            "notes": f"Fallback: {err}",
        }
        return result, _trace_call(
            name=tool_name,
            mode="live",
            messages=messages,
            tools=[schema],
            tool_choice=tool_choice,
            args=args,
            result=result,
            error=err,
            tokens=tokens,
            extra={"deterministic_precheck": det_dict, "fallback": True},
        )
    # Hard fail only on safety-critical deterministic failures (see
    # _SAFETY_CRITICAL_CHECKS above) -- content-quality checks like
    # multi-ask are left to the LLM's own independent verdict, per this
    # prompt's explicit instruction not to just copy the precheck through.
    safety_failures = [f for f in failures if f in _SAFETY_CRITICAL_CHECKS]
    if safety_failures:
        args["passed"] = False
        args["failures"] = list(set((args.get("failures") or []) + safety_failures))
        args["regenerate"] = True
    return args, _trace_call(
        name=tool_name,
        mode="live",
        messages=messages,
        tools=[schema],
        tool_choice=tool_choice,
        args=args,
        result=args,
        tokens=tokens,
        extra={"deterministic_precheck": det_dict},
    )


def resolve_llm(settings) -> Tuple[Any, bool]:
    """Return (llm_client_or_None, mock_mode)."""
    mode = (getattr(settings, "OUTCOME_AGENT_LLM_MODE", None) or "live").lower()
    if mode == "mock":
        return None, True
    try:
        key = (settings.AZURE_OPENAI_KEY or "").strip()
        endpoint = (settings.AZURE_OPENAI_ENDPOINT or "").strip()
        placeholder_markers = ("your-", "changeme", "placeholder", "example", "test-key")
        if (
            not key
            or not endpoint
            or any(m in key.lower() for m in placeholder_markers)
            or any(m in endpoint.lower() for m in placeholder_markers)
        ):
            return None, True
        from app.services.azure_openai import get_llm

        return get_llm(), False
    except Exception as exc:
        logger.warning("LLM unavailable, using mock: %s", exc)
        return None, True
