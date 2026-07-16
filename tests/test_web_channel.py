"""
Phase 2 tests: the FastAPI web channel. Backend and LLM are swapped via
FastAPI's dependency_overrides / monkeypatch — no live Azure OpenAI or
Lummus backend call.
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from fpdf import FPDF

import app.channels.web as web_module
from app.main import app
from app.services.backend_client import BackendClient, get_backend_client

BASE = "http://test-backend"


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DOCUMENTS_LOCAL_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    # /api/auth/login builds its own BackendClient from settings (it has to
    # verify arbitrary submitted credentials, not just use the shared
    # service account) -- point it at the same mocked base URL as BASE.
    monkeypatch.setenv("BACKEND_API_URL", BASE)
    # Disabled here so the other tests (which use @example.com) aren't
    # coupled to the temporary domain gate -- see test_login_domain_gate
    # below for that feature's own tests.
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    web_module._sessions.clear()

    test_backend = BackendClient(BASE, "svc@example.com", "password")
    app.dependency_overrides[get_backend_client] = lambda: test_backend

    yield TestClient(app)

    app.dependency_overrides.clear()


def make_pdf_bytes(text: str) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=12)
    pdf.add_page()
    pdf.multi_cell(0, 10, text)
    return bytes(pdf.output())


# ---------------------------------------------------------------------------

@respx.mock
def test_login_success_returns_session_token(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))

    resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})

    assert resp.status_code == 200
    assert resp.json()["email"] == "user@example.com"
    assert resp.json()["session_token"]


@respx.mock
def test_login_failure_returns_401(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(401))

    resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrong"})

    assert resp.status_code == 401


@respx.mock
def test_login_rejects_non_allowed_domain(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "corehelix.ai")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})

    assert resp.status_code == 403
    assert "corehelix.ai" in resp.json()["detail"]


@respx.mock
def test_login_rejects_lookalike_domain_suffix(client: TestClient, monkeypatch) -> None:
    """'corehelix.ai.evil.com' must not slip through a naive .endswith() check."""
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "corehelix.ai")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    resp = client.post("/api/auth/login", json={"email": "user@corehelix.ai.evil.com", "password": "pw"})

    assert resp.status_code == 403


@respx.mock
def test_login_accepts_allowed_domain_case_insensitive(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "corehelix.ai")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))

    resp = client.post("/api/auth/login", json={"email": "User@CoreHelix.AI", "password": "pw"})

    assert resp.status_code == 200


@respx.mock
def test_login_domain_gate_checked_before_backend_call(client: TestClient, monkeypatch) -> None:
    """A rejected domain shouldn't even attempt a real backend login."""
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "corehelix.ai")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    # No respx route registered for /api/v1/auth/login at all -- if the
    # handler tried to call the backend, respx would raise AllMockedAssertionError.

    resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})

    assert resp.status_code == 403


@respx.mock
def test_backend_unreachable_returns_clean_503_not_raw_500(client: TestClient) -> None:
    """A connection failure (backend down, wrong URL, network blip) is a
    different failure mode than a bad HTTP response -- BackendError's own
    handling never sees it. Regression test for a real bug found while
    smoke-testing: this used to leak a raw httpx.ConnectError as a 500."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(side_effect=httpx.ConnectError("connection refused"))

    resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})

    assert resp.status_code == 503
    assert "unreachable" in resp.json()["detail"]


def test_invoices_endpoint_requires_session(client: TestClient) -> None:
    resp = client.get("/api/invoices")
    assert resp.status_code == 401


@respx.mock
def test_invoices_endpoint_with_valid_session(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "case-1", "project_name": "Meridian Bay"}]})
    )

    resp = client.get("/api/invoices", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()[0]["project_name"] == "Meridian Bay"


def test_upload_ingest_and_search_document(client: TestClient) -> None:
    respx_router = respx.mock
    with respx_router:
        respx_router.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
        login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    pdf_bytes = make_pdf_bytes("Torque specification for bolt A-42 is 45 Nm.")
    upload_resp = client.post(
        "/api/documents/upload",
        files={"file": ("spec.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        params={"doc_type": "equipment_manual"},
        headers=headers,
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["chunks_indexed"] >= 1
    assert upload_resp.json()["pages_needing_ocr"] == 0

    search_resp = client.get("/api/documents/search", params={"query": "torque bolt A-42"}, headers=headers)
    assert search_resp.status_code == 200
    results = search_resp.json()
    assert len(results) >= 1
    assert "45 Nm" in results[0]["excerpt"]


@respx.mock
def test_chat_streams_tool_call_then_answer(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": []}))

    def make_tool_call(call_id, name, arguments):
        return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))

    def make_message(content=None, tool_calls=None):
        return SimpleNamespace(content=content, tool_calls=tool_calls)

    class ScriptedLLM:
        def __init__(self, responses):
            self._responses = list(responses)

        async def chat(self, messages, tools=None, tool_choice="auto"):
            return self._responses.pop(0)

    scripted = ScriptedLLM(
        [
            make_message(tool_calls=[make_tool_call("t1", "list_invoices", {})]),
            make_message(content="No overdue invoices found."),
        ]
    )
    import app.channels.web as web_module

    original_get_llm = web_module.get_llm
    web_module.get_llm = lambda: scripted
    try:
        resp = client.post(
            "/api/chat",
            json={"message": "any overdue invoices?"},
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        web_module.get_llm = original_get_llm

    assert resp.status_code == 200
    body = resp.text
    events = [json.loads(line[len("data: "):]) for line in body.splitlines() if line.startswith("data: ")]

    assert events[0] == {"type": "tool_call", "name": "list_invoices", "permitted": True}
    assert events[1]["type"] == "answer"
    assert events[1]["content"] == "No overdue invoices found."


@respx.mock
def test_chat_with_pinned_invoice_includes_id_in_history_not_response(client: TestClient) -> None:
    """The raw invoice_id must reach the model (via history) but the
    endpoint's own response shape never needs to surface it beyond what the
    model chooses to say -- this test locks in that the pin is wired through."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    captured_messages = {}

    def make_message(content=None, tool_calls=None):
        return SimpleNamespace(content=content, tool_calls=tool_calls)

    class CapturingLLM:
        async def chat(self, messages, tools=None, tool_choice="auto"):
            captured_messages["messages"] = messages
            return make_message(content="ok")

    import app.channels.web as web_module

    original_get_llm = web_module.get_llm
    web_module.get_llm = lambda: CapturingLLM()
    try:
        client.post(
            "/api/chat",
            json={
                "message": "what's the status?",
                "pinned_invoice": {"invoice_id": "case-42", "label": "Meridian Bay -- $1.25M, 21 days overdue"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        web_module.get_llm = original_get_llm

    joined = json.dumps(captured_messages["messages"])
    assert "case-42" in joined
