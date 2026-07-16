"""
Phase 1 tests: the agent loop, with the LLM fully mocked (a scripted fake)
and the backend mocked via respx. No live model or backend calls — this is
what lets Phase 1 be built and verified before the real Azure OpenAI key
arrives (see PLAN.md §7).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.agent.loop import AgentLoop, MAX_TOOL_ROUNDS
from app.agent.setup import build_registry
from app.services.backend_client import BackendClient

BASE = "http://test-backend"


def make_tool_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def make_message(content=None, tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class ScriptedLLM:
    """Returns pre-scripted responses in order; records what it was called with."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls.append({"messages": [dict(m) if isinstance(m, dict) else m for m in messages], "tools": tools})
        return self._responses.pop(0)


@pytest.fixture
def backend() -> BackendClient:
    return BackendClient(BASE, "svc@example.com", "password")


def _mock_login():
    respx.post(f"{BASE}/api/v1/auth/login").mock(
        return_value=httpx.Response(200, json={"access_token": "fake-token"})
    )


@pytest.mark.asyncio
@respx.mock
async def test_multi_step_tool_chaining(backend: BackendClient) -> None:
    """Model lists invoices, then pulls the timeline for the one it picked,
    then answers -- the exact multi-step shape PLAN.md §5 Phase 1 requires."""
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "case-1",
                        "case_key": "CK-1",
                        "case_status": "active",
                        "current_stage_code": "final_notice",
                        "project_name": "Meridian Bay Terminal Expansion",
                        "primary_invoice_due_date": "2026-06-01",
                        "primary_invoice_open_amount": 1_250_000,
                        "primary_invoice_aging_status": "121+",
                    }
                ]
            },
        )
    )
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1/timeline").mock(
        return_value=httpx.Response(200, json={"items": [{"event_type": "reply_received", "event_title": "PM replied"}]})
    )

    llm = ScriptedLLM(
        [
            make_message(tool_calls=[make_tool_call("t1", "list_invoices", {"overdue_days_min": 60})]),
            make_message(tool_calls=[make_tool_call("t2", "get_timeline", {"invoice_id": "case-1"})]),
            make_message(content="Meridian Bay is worst, and yes their PM replied."),
        ]
    )
    registry = build_registry()
    loop = AgentLoop(llm=llm, registry=registry, backend_client=backend)

    result = await loop.run("which project has the most money stuck past 60 days, and has its PM replied?")

    assert result.answer == "Meridian Bay is worst, and yes their PM replied."
    assert not result.truncated
    assert [tc.name for tc in result.tool_calls] == ["list_invoices", "get_timeline"]
    assert all(tc.permitted for tc in result.tool_calls)
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_write_tool_call_is_rejected_not_silently_dropped(backend: BackendClient) -> None:
    """Phase 1 registers zero write tools. If the model tries one anyway
    (hallucinated or otherwise), the loop must feed back an explicit
    rejection, not silently ignore the call."""
    llm = ScriptedLLM(
        [
            make_message(tool_calls=[make_tool_call("t1", "snooze_invoice", {"invoice_id": "case-1"})]),
            make_message(content="I can look things up but can't act yet."),
        ]
    )
    registry = build_registry()
    loop = AgentLoop(llm=llm, registry=registry, backend_client=backend)

    result = await loop.run("snooze the Meridian Bay invoice for 2 weeks")

    assert len(result.tool_calls) == 1
    rejected = result.tool_calls[0]
    assert rejected.name == "snooze_invoice"
    assert rejected.permitted is False
    assert "not permitted" in rejected.result["error"]

    # The rejection must have been fed back to the model as a real tool
    # response (not skipped), i.e. the second .chat() call's messages
    # contain a role="tool" message referencing it.
    second_call_messages = llm.calls[1]["messages"]
    tool_messages = [m for m in second_call_messages if isinstance(m, dict) and m.get("role") == "tool"]
    assert any("not permitted" in tm["content"] for tm in tool_messages)
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_stops_at_max_rounds_and_answers_with_what_it_has(backend: BackendClient) -> None:
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    _mock_login()

    # Always requests another tool call -- never converges on its own.
    endless_tool_calls = [
        make_message(tool_calls=[make_tool_call(f"t{i}", "list_invoices", {})]) for i in range(MAX_TOOL_ROUNDS)
    ]
    final_forced_answer = make_message(content="Here's what I found before running out of budget.")
    llm = ScriptedLLM(endless_tool_calls + [final_forced_answer])

    registry = build_registry()
    loop = AgentLoop(llm=llm, registry=registry, backend_client=backend, max_rounds=MAX_TOOL_ROUNDS)

    result = await loop.run("keep looking forever")

    assert result.truncated is True
    assert result.answer == "Here's what I found before running out of budget."
    assert len(result.tool_calls) == MAX_TOOL_ROUNDS
    # The forced final call must not offer tools -- otherwise it could loop forever.
    assert llm.calls[-1]["tools"] is None
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_on_tool_call_hook_fires_for_each_call(backend: BackendClient) -> None:
    """This is what lets the web channel (Phase 2) stream progress over SSE
    without the LLM itself supporting token streaming."""
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": []}))

    llm = ScriptedLLM(
        [
            make_message(tool_calls=[make_tool_call("t1", "list_invoices", {})]),
            make_message(content="done"),
        ]
    )
    seen = []

    async def on_tool_call(record):
        seen.append(record.name)

    registry = build_registry()
    loop = AgentLoop(llm=llm, registry=registry, backend_client=backend)

    result = await loop.run("question", on_tool_call=on_tool_call)

    assert seen == ["list_invoices"]
    assert result.answer == "done"
    await backend.close()
