"""Azure OpenAI forced-tool adapters for interpret / plan / draft / judge."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.outcome_agent.loop.reply_interpreter import DeterministicReplyInterpreter, InterpretedReply

logger = logging.getLogger(__name__)


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
) -> Tuple[InterpretedReply, Dict[str, Any]]:
    """Returns (interpretation, call_trace)."""
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
                f"Call {tool_name} exactly once. Prefer concrete dates (YYYY-MM-DD)."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "reply_text": text,
                    "invoice": (context.get("case") or {}).get("invoice_no"),
                    "world": context.get("world"),
                }
            ),
        },
    ]
    tool_choice = {"type": "function", "function": {"name": tool_name}}
    if mock or llm is None:
        det = DeterministicReplyInterpreter().interpret(text)
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
        det = DeterministicReplyInterpreter().interpret(text)
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
    from app.outcome_agent.loop.communication import DEFAULT_GENERATOR

    tool_name = "record_email_draft"
    inv = case.get("invoice_no") or "invoice"
    subject = f"[{case.get('subject_token')}] Invoice {inv}"
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
    messages = [
        {
            "role": "system",
            "content": (
                "Write a concise, professional, non-threatening AR email. "
                "Ask for exactly one concrete next step. "
                f"Call {tool_name} exactly once. Never threaten legal action or offer discounts."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "invoice": inv,
                    "amount": case.get("amount"),
                    "customer": case.get("customer_name"),
                    "objective": plan.get("objective"),
                    "tactic": plan.get("selected_tactic"),
                    "latest_inbound": (context.get("dialogue") or {}).get("latest_inbound"),
                    "policy_snippets": context.get("relevant_policy")
                    or context.get("policy_snippets")
                    or [],
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
                "Judge AR email drafts. Fail if threatening, multi-ask, or missing invoice ref. "
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
    # Hard fail if deterministic critic failed (safety)
    if not det.passed:
        args["passed"] = False
        args["failures"] = list(set((args.get("failures") or []) + failures))
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
        if not key or not endpoint or "your-" in key.lower() or key == "changeme":
            return None, True
        from app.services.azure_openai import get_llm

        return get_llm(), False
    except Exception as exc:
        logger.warning("LLM unavailable, using mock: %s", exc)
        return None, True
