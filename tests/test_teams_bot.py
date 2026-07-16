"""
Phase 4 tests: TeamsBot. Real registry + respx-mocked backend + FakeMessenger
+ real ProjectConversationStore (temp SQLite) -- everything except an actual
Bot Framework connection and a real LLM (scripted).

2026-07-16: snooze/comment no longer happen via in-Teams form cards -- a
message expressing that intent gets a magic-link redirect card to the web
invoice picker for the chat's project instead (see bot.py's
_detect_write_intent/_redirect_to_web). Only the plain-text AgentLoop path
and the disambiguation-select card path remain as before.
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
from app.channels.teams.project_conversation_store import ProjectConversationStore
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


@pytest.fixture
def project_store(tmp_path) -> ProjectConversationStore:
    return ProjectConversationStore(db_path=str(tmp_path / "state.db"))


@pytest.mark.asyncio
@respx.mock
async def test_plain_text_routes_through_agent_loop(backend, messenger, project_store, monkeypatch):
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
    bot = TeamsBot(AgentLoop(llm=llm, registry=build_registry(), backend_client=backend), messenger, project_store)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM", text="any overdue invoices?")

    await bot.handle(activity)

    assert len(messenger.sent) == 1
    assert messenger.sent[0].text == "No overdue invoices."
    await backend.close()


@pytest.mark.asyncio
async def test_snooze_intent_in_project_chat_redirects_to_invoice_picker(backend, messenger, project_store):
    await project_store.record("PN-1", "conv-1")
    bot = TeamsBot(AgentLoop(llm=None, registry=build_registry(), backend_client=backend), messenger, project_store)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM", text="I want to snooze one of these")

    await bot.handle(activity)

    assert len(messenger.sent) == 1
    card = messenger.sent[0].card
    assert card is not None
    assert card["actions"][0]["type"] == "Action.OpenUrl"
    assert "/link?token=" in card["actions"][0]["url"]
    await backend.close()


@pytest.mark.asyncio
async def test_comment_intent_in_project_chat_redirects_to_invoice_picker(backend, messenger, project_store):
    await project_store.record("PN-1", "conv-1")
    bot = TeamsBot(AgentLoop(llm=None, registry=build_registry(), backend_client=backend), messenger, project_store)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM", text="can I add a note about this?")

    await bot.handle(activity)

    assert len(messenger.sent) == 1
    assert messenger.sent[0].card["actions"][0]["type"] == "Action.OpenUrl"
    await backend.close()


@pytest.mark.asyncio
async def test_write_intent_in_unlinked_chat_explains_instead_of_guessing(backend, messenger, project_store):
    # No project_store.record() call -- this conversation isn't a known project chat.
    bot = TeamsBot(AgentLoop(llm=None, registry=build_registry(), backend_client=backend), messenger, project_store)
    activity = IncomingActivity(conversation_id="conv-unlinked", user_id="pm@corehelix.ai", user_name="A PM", text="snooze this please")

    await bot.handle(activity)  # must not raise

    assert len(messenger.sent) == 1
    assert messenger.sent[0].text is not None
    assert messenger.sent[0].card is None
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_disambiguation_select_continues_conversation(backend, messenger, project_store, monkeypatch):
    monkeypatch.setenv("ADMIN_UPNS", "admin@corehelix.ai")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    llm = ScriptedLLM([make_message(content="Meridian Bay is 21 days overdue, $1.25M open.")])
    bot = TeamsBot(AgentLoop(llm=llm, registry=build_registry(), backend_client=backend), messenger, project_store)
    activity = IncomingActivity(
        conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
        card_data={"action": "disambiguate_select", "invoice_id": "case-1", "label": "Meridian Bay"},
    )

    await bot.handle(activity)

    assert messenger.sent[0].text == "Meridian Bay is 21 days overdue, $1.25M open."
    await backend.close()


@pytest.mark.asyncio
async def test_unrecognized_action_sends_error_card(backend, messenger, project_store):
    bot = TeamsBot(AgentLoop(llm=None, registry=build_registry(), backend_client=backend), messenger, project_store)
    activity = IncomingActivity(
        conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
        card_data={"action": "something_unexpected"},
    )

    await bot.handle(activity)

    assert "Unrecognized action" in messenger.sent[0].card["body"][0]["text"]
    await backend.close()
