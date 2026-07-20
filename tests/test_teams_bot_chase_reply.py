"""Tests: TeamsBot's chase-reply pre-check (added 2026-07-20, Phase C3).
Real ChaseStore/ProjectConversationStore (temp SQLite) + FakeMessenger +
respx-mocked backend + ScriptedLLM for the chase parser's forced tool
call, same idiom as test_teams_bot.py and test_chase_engine.py."""
from __future__ import annotations

import json
from datetime import date, timedelta
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
from app.services.chase_store import ChaseStore
from app.services.email_sender import FakeEmailSender

BASE = "http://test-backend"


def _mock_login():
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))


def make_tool_call(name: str, arguments: dict):
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class ScriptedLLM:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    async def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls += 1
        return self._response


@pytest.fixture
def backend() -> BackendClient:
    return BackendClient(BASE, "svc@example.com", "password")


@pytest.fixture
def messenger() -> FakeMessenger:
    return FakeMessenger()


@pytest.fixture
def project_store(tmp_path) -> ProjectConversationStore:
    return ProjectConversationStore(db_path=str(tmp_path / "state.db"))


@pytest.fixture
def chase_store(tmp_path) -> ChaseStore:
    return ChaseStore(db_path=str(tmp_path / "state.db"))


@pytest.fixture
def email_sender() -> FakeEmailSender:
    return FakeEmailSender()


def make_bot(backend, messenger, project_store, chase_store, email_sender, llm) -> TeamsBot:
    loop = AgentLoop(llm=llm, registry=build_registry(), backend_client=backend)
    return TeamsBot(
        loop, messenger, project_store,
        chase_store=chase_store, backend_client=backend, email_sender=email_sender, llm=llm,
    )


@pytest.mark.asyncio
@respx.mock
async def test_reply_in_project_chat_with_one_open_chase_is_treated_as_chase_reply(
    backend, messenger, project_store, chase_store, email_sender, monkeypatch
):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    monkeypatch.setenv("CHASE_DRY_RUN", "false")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    await project_store.record("PN-1", "conv-1")
    chase_id = await chase_store.create("case-1", invoice_no="INV-1", project_number="PN-1")
    await chase_store.update(chase_id, state="awaiting_pm", target="pm", pm_email="pm@corehelix.ai")

    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "commitment_date", "confidence": "high",
            "promised_date": (date.today() + timedelta(days=7)).isoformat(),
        })
    ]))
    bot = make_bot(backend, messenger, project_store, chase_store, email_sender, llm)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
                                 text="Customer says they'll pay in a week.")

    await bot.handle(activity)

    chase = await chase_store.get(chase_id)
    assert chase["state"] == "commitment_tracked"
    # Confirmation reply went out via Teams (project chat known).
    assert len(messenger.sent) == 1
    assert "Got it" in messenger.sent[0].text
    await backend.close()


@pytest.mark.asyncio
async def test_question_in_project_chat_with_open_chase_falls_through_to_agent_loop(
    backend, messenger, project_store, chase_store, email_sender
):
    await project_store.record("PN-1", "conv-1")
    chase_id = await chase_store.create("case-1", invoice_no="INV-1", project_number="PN-1")
    await chase_store.update(chase_id, state="awaiting_pm", target="pm")

    llm = ScriptedLLM(SimpleNamespace(content="Here's the aging summary.", tool_calls=None))
    bot = make_bot(backend, messenger, project_store, chase_store, email_sender, llm)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
                                 text="what's the aging summary for this project?")

    await bot.handle(activity)

    # Chase untouched -- this went through the normal agent loop, not the parser.
    chase = await chase_store.get(chase_id)
    assert chase["state"] == "awaiting_pm"
    assert messenger.sent[0].text == "Here's the aging summary."
    await backend.close()


@pytest.mark.asyncio
async def test_no_open_chase_falls_through_to_normal_handling(
    backend, messenger, project_store, chase_store, email_sender
):
    await project_store.record("PN-1", "conv-1")
    # No chase created at all.

    llm = ScriptedLLM(SimpleNamespace(content="Sure, here's the info.", tool_calls=None))
    bot = make_bot(backend, messenger, project_store, chase_store, email_sender, llm)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
                                 text="Should be fine, will check on it.")

    await bot.handle(activity)

    assert messenger.sent[0].text == "Sure, here's the info."
    await backend.close()


@pytest.mark.asyncio
async def test_chat_with_no_chase_deps_configured_behaves_as_before(backend, messenger, project_store):
    """Backward-compat: a TeamsBot built the old way (no chase_store/
    backend_client/email_sender/llm passed) must never touch the chase
    machinery at all -- covers every pre-Phase-C3 call site/test."""
    llm = ScriptedLLM(SimpleNamespace(content="Old behavior preserved.", tool_calls=None))
    bot = TeamsBot(AgentLoop(llm=llm, registry=build_registry(), backend_client=backend), messenger, project_store)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM", text="hello there")

    await bot.handle(activity)

    assert messenger.sent[0].text == "Old behavior preserved."
    await backend.close()


@pytest.mark.asyncio
async def test_multiple_open_chases_with_no_invoice_mentioned_asks_which_one(
    backend, messenger, project_store, chase_store, email_sender
):
    await project_store.record("PN-1", "conv-1")
    chase_a = await chase_store.create("case-1", invoice_no="INV-1", project_number="PN-1")
    await chase_store.update(chase_a, state="awaiting_pm", target="pm")
    chase_b = await chase_store.create("case-2", invoice_no="INV-2", project_number="PN-1")
    await chase_store.update(chase_b, state="awaiting_pm", target="pm")

    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=None))
    bot = make_bot(backend, messenger, project_store, chase_store, email_sender, llm)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
                                 text="Should be paid soon.")

    await bot.handle(activity)

    assert "INV-1" in messenger.sent[0].text and "INV-2" in messenger.sent[0].text
    assert llm.calls == 0  # never even asked the parser -- disambiguation short-circuits first
    # Neither chase was touched.
    assert (await chase_store.get(chase_a))["state"] == "awaiting_pm"
    assert (await chase_store.get(chase_b))["state"] == "awaiting_pm"
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_multiple_open_chases_disambiguated_by_invoice_number_in_reply(
    backend, messenger, project_store, chase_store, email_sender, monkeypatch
):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    _mock_login()
    respx.post(f"{BASE}/api/v2/dunning/response-events").mock(return_value=httpx.Response(200, json={"ok": True}))
    await project_store.record("PN-1", "conv-1")
    chase_a = await chase_store.create("case-1", invoice_no="INV-1", project_number="PN-1")
    await chase_store.update(chase_a, state="awaiting_pm", target="pm")
    chase_b = await chase_store.create("case-2", invoice_no="INV-2", project_number="PN-1")
    await chase_store.update(chase_b, state="awaiting_pm", target="pm")

    llm = ScriptedLLM(SimpleNamespace(content=None, tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "dispute", "confidence": "high"})
    ]))
    bot = make_bot(backend, messenger, project_store, chase_store, email_sender, llm)
    activity = IncomingActivity(conversation_id="conv-1", user_id="pm@corehelix.ai", user_name="A PM",
                                 text="We're disputing INV-2, the amount is wrong.")

    await bot.handle(activity)

    assert (await chase_store.get(chase_a))["state"] == "awaiting_pm"
    assert (await chase_store.get(chase_b))["state"] == "escalated"
    await backend.close()
