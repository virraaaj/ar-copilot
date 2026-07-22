"""Tests: chase_trajectory.py (added 2026-07-22). ScriptedLLM idiom
matching chase_parser.py's tests -- forced tool call, (message, tokens)
return_usage shape."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.chase_trajectory import TrajectoryAssessment, assess_chase_trajectory


def make_tool_call(name: str, arguments: dict):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class ScriptedLLM:
    def __init__(self, response, tokens=77, raise_exc=None):
        self._response = response
        self._tokens = tokens
        self._raise = raise_exc
        self.calls = []

    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if self._raise:
            raise self._raise
        return self._response, self._tokens


def base_chase(**overrides):
    chase = {"invoice_no": "INV-1", "missed_count": 0, "nudge_count": 0}
    chase.update(overrides)
    return chase


@pytest.mark.asyncio
async def test_progressing_verdict():
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "progressing", "reason": "PM is actively engaging"})
    ]), tokens=100)

    assessment, tokens = await assess_chase_trajectory(llm, base_chase(), [])

    assert assessment == TrajectoryAssessment(verdict="progressing", reason="PM is actively engaging")
    assert tokens == 100


@pytest.mark.asyncio
async def test_concerning_verdict():
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "concerning", "reason": "Hostile tone, disputing the invoice"})
    ]))

    assessment, tokens = await assess_chase_trajectory(llm, base_chase(), [])

    assert assessment.verdict == "concerning"


@pytest.mark.asyncio
async def test_stalling_verdict():
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "stalling", "reason": "Same vague answer repeated"})
    ]))

    assessment, tokens = await assess_chase_trajectory(llm, base_chase(), [])

    assert assessment.verdict == "stalling"


@pytest.mark.asyncio
async def test_forces_the_tool_choice():
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "progressing", "reason": "ok"})
    ]))

    await assess_chase_trajectory(llm, base_chase(), [])

    assert llm.calls[0]["tool_choice"] == {"type": "function", "function": {"name": "record_trajectory_assessment"}}


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_progressing():
    llm = ScriptedLLM(None, raise_exc=RuntimeError("boom"))

    assessment, tokens = await assess_chase_trajectory(llm, base_chase(), [])

    assert assessment.verdict == "progressing"
    assert tokens == 0


@pytest.mark.asyncio
async def test_no_tool_call_falls_back_to_progressing():
    llm = ScriptedLLM(SimpleNamespace(content="not a tool call", tool_calls=None))

    assessment, tokens = await assess_chase_trajectory(llm, base_chase(), [])

    assert assessment.verdict == "progressing"


@pytest.mark.asyncio
async def test_invalid_verdict_falls_back_to_progressing():
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "furious", "reason": "made up"})
    ]))

    assessment, tokens = await assess_chase_trajectory(llm, base_chase(), [])

    assert assessment.verdict == "progressing"


@pytest.mark.asyncio
async def test_malformed_arguments_falls_back_to_progressing():
    bad_call = SimpleNamespace(function=SimpleNamespace(name="record_trajectory_assessment", arguments="{not json"))
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[bad_call]))

    assessment, tokens = await assess_chase_trajectory(llm, base_chase(), [])

    assert assessment.verdict == "progressing"


@pytest.mark.asyncio
async def test_history_is_formatted_into_the_prompt():
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "progressing", "reason": "ok"})
    ]))
    events = [
        {"kind": "outreach_sent", "detail": {"target": "pm", "text": "Invoice is overdue, any update?"}},
        {"kind": "reply_received", "detail": {"target": "pm", "text": "We'll pay next week."}},
    ]

    await assess_chase_trajectory(llm, base_chase(), events)

    user_content = llm.calls[0]["messages"][1]["content"]
    assert "Invoice is overdue, any update?" in user_content
    assert "We'll pay next week." in user_content


@pytest.mark.asyncio
async def test_empty_history_does_not_crash():
    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_trajectory_assessment", {"verdict": "progressing", "reason": "no messages yet"})
    ]))

    assessment, tokens = await assess_chase_trajectory(llm, base_chase(), [])

    assert assessment.verdict == "progressing"
