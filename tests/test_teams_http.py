"""Tests: POST /api/messages, the inbound Bot Framework HTTP edge (added
2026-07-17). JWT validation is monkeypatched to a stub (already covered
for real in test_teams_auth.py) so these focus on activity parsing,
serviceUrl capture, and routing into TeamsBot -- with a real
ProjectConversationStore (temp SQLite), FakeMessenger, and a scripted LLM,
same pattern as test_teams_bot.py."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.channels.teams.http as teams_http_module
from app.channels.teams.messenger import FakeMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.main import app
from app.services.backend_client import BackendClient, get_backend_client

BASE = "http://test-backend"


def make_message(content=None, tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


class ScriptedLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat(self, messages, tools=None, tool_choice="auto"):
        return self._responses.pop(0)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    monkeypatch.setenv("MICROSOFT_APP_ID", "test-app-id")
    monkeypatch.setenv("MICROSOFT_APP_PASSWORD", "")  # stays FakeMessenger
    monkeypatch.setenv("MICROSOFT_APP_TENANT_ID", "test-tenant-id")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    # Real JWT verification is covered in test_teams_auth.py -- here we
    # just need the endpoint to treat a request as authenticated.
    monkeypatch.setattr(teams_http_module, "validate_bot_framework_token", lambda header: {"sub": "user-1"})

    fake_messenger = FakeMessenger()
    monkeypatch.setattr(teams_http_module, "get_messenger", lambda: fake_messenger)

    test_backend = BackendClient(BASE, "svc@example.com", "password")
    app.dependency_overrides[get_backend_client] = lambda: test_backend

    tc = TestClient(app)
    tc.fake_messenger = fake_messenger  # type: ignore[attr-defined]
    yield tc

    app.dependency_overrides.clear()


def _activity(**overrides):
    base = {
        "type": "message",
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "conversation": {"id": "conv-1"},
        "from": {"id": "29:abc", "aadObjectId": "aad-1", "name": "Viraj"},
        "text": "hello",
    }
    base.update(overrides)
    return base


def test_missing_auth_header_is_accepted_by_this_fixtures_stub_validator(client):
    # The monkeypatched validator always succeeds in this fixture (real
    # rejection is covered in test_teams_auth.py and via
    # test_invalid_token_returns_401 below) -- uses a non-"message" activity
    # so it doesn't need an LLM mocked, since this is only checking that
    # the header itself is optional at the transport level.
    resp = client.post("/api/messages", json=_activity(type="conversationUpdate", text=None))
    assert resp.status_code == 200


def test_invalid_token_returns_401(client, monkeypatch):
    from app.channels.teams.auth import TokenValidationError

    def _raise(header):
        raise TokenValidationError("bad token")

    monkeypatch.setattr(teams_http_module, "validate_bot_framework_token", _raise)

    resp = client.post("/api/messages", headers={"Authorization": "Bearer bad"}, json=_activity())

    assert resp.status_code == 401


def test_records_service_url_for_any_activity_type(client, tmp_path):
    resp = client.post(
        "/api/messages",
        headers={"Authorization": "Bearer whatever"},
        json=_activity(type="conversationUpdate", text=None),
    )

    assert resp.status_code == 200

    import asyncio

    store = ProjectConversationStore()

    async def check():
        return await store.get_service_url("conv-1")

    assert asyncio.run(check()) == "https://smba.trafficmanager.net/amer/"


def test_conversation_update_does_not_invoke_agent_loop(client, monkeypatch):
    called = {"count": 0}

    async def fake_run(self, *args, **kwargs):
        called["count"] += 1
        return SimpleNamespace(answer="should not be called", tool_calls=[], truncated=False)

    from app.agent.loop import AgentLoop

    monkeypatch.setattr(AgentLoop, "run", fake_run)

    resp = client.post(
        "/api/messages",
        headers={"Authorization": "Bearer whatever"},
        json=_activity(type="conversationUpdate", text=None),
    )

    assert resp.status_code == 200
    assert called["count"] == 0


@respx.mock
def test_plain_text_message_routes_through_agent_loop_and_replies(client, monkeypatch):
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))

    scripted = ScriptedLLM([make_message(content="Hi there, how can I help?")])
    monkeypatch.setattr(teams_http_module, "get_llm", lambda: scripted)

    resp = client.post("/api/messages", headers={"Authorization": "Bearer whatever"}, json=_activity(text="hi"))

    assert resp.status_code == 200
    assert len(client.fake_messenger.sent) == 1
    assert client.fake_messenger.sent[0].conversation_id == "conv-1"
    assert client.fake_messenger.sent[0].text == "Hi there, how can I help?"


@respx.mock
def test_action_submit_card_data_is_routed_as_card_data(client, monkeypatch):
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))

    resp = client.post(
        "/api/messages",
        headers={"Authorization": "Bearer whatever"},
        json=_activity(text=None, value={"action": "unknown_action"}),
    )

    assert resp.status_code == 200
    assert len(client.fake_messenger.sent) == 1
    assert "Unrecognized action" in json.dumps(client.fake_messenger.sent[0].card)
