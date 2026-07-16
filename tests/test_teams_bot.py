"""
Phase 4 tests: TeamsBot. Real registry + real write tools + respx-mocked
backend + FakeMessenger -- everything except an actual Bot Framework
connection and a real LLM (scripted), matching the pattern used throughout
this project for credential-blocked integrations.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.agent.loop import AgentLoop
from app.agent.setup import build_registry
from app.channels.teams.bot import IncomingActivity, TeamsBot
from app.channels.teams.messenger import FakeMessenger
from app.services.backend_client import BackendClient

BASE = "http://test-backend"


def make_tool_call(call_id: str, name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def make_message(content=None, tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat(self, messages, tools=None, tool_choice="auto"):
        return self._responses.pop(0)


def _mock_login():
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))


@pytest.fixture
def backend() -> BackendClient:
    return BackendClient(BASE, "svc@example.com", "password")


@pytest.fixture
def messenger() -> FakeMessenger:
    return FakeMessenger()


@pytest.mark.asyncio
@respx.mock
async def test_plain_text_routes_through_agent_loop(backend, messenger, monkeypatch):
    monkeypatch.setenv("ADMIN_UPNS", "admin@corehelix.ai")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": []}))

    llm = ScriptedLLM(
        [
            make_message(tool_calls=[make_tool_call("t1", "list_invoices", {})]),
            make_message(content="No overdue invoices."),
        ]
    )
    bot = TeamsBot(AgentLoop(llm=llm, registry=build_registry(), backend_client=backend), messenger, backend)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM", text="any overdue invoices?")

    await bot.handle(activity)

    assert len(messenger.sent) == 1
    assert messenger.sent[0].text == "No overdue invoices."
    await backend.close()


@pytest.mark.asyncio
async def test_snooze_button_opens_form_card(backend, messenger):
    bot = TeamsBot(AgentLoop(llm=None, registry=build_registry(), backend_client=backend), messenger, backend)
    activity = IncomingActivity(
        conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
        card_data={"action": "open_snooze_form", "invoice_id": "case-1", "label": "Meridian Bay"},
    )

    await bot.handle(activity)

    assert len(messenger.sent) == 1
    assert messenger.sent[0].card["body"][0]["text"] == "⏸ Snooze a follow-up"
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_snooze_submit_success_sends_confirmation(backend, messenger):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(
        return_value=httpx.Response(200, json={"id": "case-1", "current_stage_code": "first_notice"})
    )
    respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause").mock(return_value=httpx.Response(200, json={"ok": True}))

    bot = TeamsBot(AgentLoop(llm=None, registry=build_registry(), backend_client=backend), messenger, backend)
    activity = IncomingActivity(
        conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
        card_data={"action": "snooze_submit", "invoice_id": "case-1", "label": "Meridian Bay", "reason": "dispute", "resume_date": "2026-08-01"},
    )

    await bot.handle(activity)

    assert len(messenger.sent) == 1
    card = messenger.sent[0].card
    assert card["body"][0]["text"] == "✅ Snoozed"
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_snooze_submit_pre_due_sends_error_card_not_exception(backend, messenger):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(
        return_value=httpx.Response(200, json={"id": "case-1", "current_stage_code": "S0_pre_due"})
    )
    pause_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause")

    bot = TeamsBot(AgentLoop(llm=None, registry=build_registry(), backend_client=backend), messenger, backend)
    activity = IncomingActivity(
        conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
        card_data={"action": "snooze_submit", "invoice_id": "case-1", "label": "Meridian Bay", "reason": "dispute"},
    )

    await bot.handle(activity)  # must not raise

    assert not pause_route.called
    card = messenger.sent[0].card
    assert "Pre-Due" in card["body"][0]["text"]
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_comment_submit_success_sends_confirmation(backend, messenger):
    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))

    bot = TeamsBot(AgentLoop(llm=None, registry=build_registry(), backend_client=backend), messenger, backend)
    activity = IncomingActivity(
        conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
        card_data={"action": "comment_submit", "invoice_id": "case-1", "label": "Meridian Bay", "comment": "Paying next week"},
    )

    await bot.handle(activity)

    card = messenger.sent[0].card
    assert "Paying next week" in card["body"][2]["text"]
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_disambiguation_select_continues_conversation(backend, messenger, monkeypatch):
    monkeypatch.setenv("ADMIN_UPNS", "admin@corehelix.ai")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    llm = ScriptedLLM([make_message(content="Meridian Bay is 21 days overdue, $1.25M open.")])
    bot = TeamsBot(AgentLoop(llm=llm, registry=build_registry(), backend_client=backend), messenger, backend)
    activity = IncomingActivity(
        conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
        card_data={"action": "disambiguate_select", "invoice_id": "case-1", "label": "Meridian Bay"},
    )

    await bot.handle(activity)

    assert messenger.sent[0].text == "Meridian Bay is 21 days overdue, $1.25M open."
    await backend.close()


@pytest.mark.asyncio
async def test_unrecognized_action_sends_error_card(backend, messenger):
    bot = TeamsBot(AgentLoop(llm=None, registry=build_registry(), backend_client=backend), messenger, backend)
    activity = IncomingActivity(
        conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
        card_data={"action": "something_unexpected"},
    )

    await bot.handle(activity)

    assert "Unrecognized action" in messenger.sent[0].card["body"][0]["text"]
    await backend.close()
