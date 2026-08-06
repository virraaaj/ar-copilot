"""
Smart escalation judgment for the chase engine (added 2026-07-22). The
deterministic nudge/miss-count budgets (chase_machine.py) stay the hard
backstop -- a chase can never run longer than those caps no matter what.
This module adds a *second*, earlier signal: given the whole real
conversation for an invoice (not just the latest reply), assess whether
it's actually heading toward resolution, going nowhere, or actively
concerning (evasive, contradictory, hostile, dispute-adjacent) -- and
escalate right away if so, rather than waiting out a fixed counter that
doesn't know the difference between "PM is slow but engaged" and "PM
has gone dark."

Deliberately one-directional: this can only ever escalate *sooner* than
the hard caps would, never grant *more* patience than they allow.
chase_engine.py is the only caller, and it always still runs the
deterministic on_nudge_check/on_commitment_due path when this returns
"progressing" or when it fails for any reason -- so a broken or absent
LLM degrades to exactly today's behavior, never to the previous
(unsafe) direction of running longer than the caps permit.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

VALID_VERDICTS = ("progressing", "stalling", "concerning")

_TOOL_NAME = "record_trajectory_assessment"

_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": _TOOL_NAME,
        "description": "Record an assessment of whether this AR follow-up conversation is heading toward resolution.",
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": list(VALID_VERDICTS),
                    "description": (
                        "progressing: there's a real back-and-forth heading toward a resolution, even if slow. "
                        "stalling: vague, repetitive, or unresponsive -- not moving anywhere, but not hostile either. "
                        "concerning: evasive, contradictory, hostile, or dispute-adjacent -- needs a human now."
                    ),
                },
                "reason": {"type": "string", "description": "One short sentence explaining the verdict."},
            },
            "required": ["verdict", "reason"],
        },
    },
}


@dataclass(frozen=True)
class TrajectoryAssessment:
    verdict: str
    reason: str


def _fallback() -> TrajectoryAssessment:
    # "progressing" is the safe default on any failure -- it defers to
    # the existing deterministic budget logic rather than forcing an
    # early escalation off a broken signal.
    return TrajectoryAssessment(verdict="progressing", reason="assessment unavailable, deferring to nudge/miss budget")


def _format_history(events: List[Dict[str, Any]]) -> str:
    lines = []
    for e in events:
        detail = e.get("detail") or {}
        if e.get("kind") in ("outreach_sent", "action_decided"):
            text = detail.get("text")
            if text:
                lines.append(f"[sent to {detail.get('target', 'unknown')}]: {text}")
        elif e.get("kind") == "reply_received":
            text = detail.get("text")
            if text:
                lines.append(f"[reply from {detail.get('target', 'unknown')}]: {text}")
    return "\n".join(lines) if lines else "(no messages exchanged yet)"


def _build_messages(chase: Dict[str, Any], events: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    invoice_ref = chase.get("invoice_no") or chase.get("case_key") or "the invoice"
    system = (
        "You assess accounts-receivable follow-up conversations to decide if they're heading toward "
        "resolution or need a human to step in. Call record_trajectory_assessment exactly once. "
        f"This conversation is about invoice {invoice_ref}, currently {chase.get('missed_count') or 0} "
        f"missed commitment(s) and {chase.get('nudge_count') or 0} unanswered follow-up(s) so far."
    )
    history = _format_history(events)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Conversation so far:\n{history}"},
    ]


async def assess_chase_trajectory(
    llm: Any, chase: Dict[str, Any], events: List[Dict[str, Any]]
) -> Tuple[TrajectoryAssessment, int]:
    """Returns (assessment, tokens_used). Never raises -- any failure
    (LLM error, malformed tool call, unrecognized verdict) returns the
    safe "progressing" fallback with 0 tokens charged."""
    messages = _build_messages(chase, events)

    try:
        result = await llm.chat(
            messages,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "function", "function": {"name": _TOOL_NAME}},
            return_usage=True,
        )
    except Exception:
        logger.exception("chase_trajectory: LLM call failed, falling back to 'progressing'")
        return _fallback(), 0

    try:
        message, tokens = result
    except (TypeError, ValueError):
        message, tokens = result, 0

    tool_calls = getattr(message, "tool_calls", None)
    if not tool_calls:
        logger.warning("chase_trajectory: model did not call %s despite forced tool_choice", _TOOL_NAME)
        return _fallback(), tokens

    try:
        args = json.loads(tool_calls[0].function.arguments or "{}")
    except (json.JSONDecodeError, AttributeError):
        logger.warning("chase_trajectory: could not parse tool call arguments")
        return _fallback(), tokens

    verdict = args.get("verdict")
    if verdict not in VALID_VERDICTS:
        return _fallback(), tokens

    reason = args.get("reason") or ""
    return TrajectoryAssessment(verdict=verdict, reason=reason), tokens
