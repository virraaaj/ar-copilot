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


@respx.mock
def test_get_invoice_timeline(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v2/dunning/cases/case-1/timeline").mock(
        return_value=httpx.Response(
            200,
            json={"items": [{"event_type": "reply_received", "event_title": "Inbound reply received", "event_summary": "Paying Friday"}]},
        )
    )

    resp = client.get("/api/invoices/case-1/timeline", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()[0]["summary"] == "Paying Friday"


@respx.mock
def test_add_comment_endpoint_uses_correct_backend_field_names(client: TestClient) -> None:
    """Regression test for the real bug found this session: an earlier
    version of log_response_event sent response_category/raw_text, neither
    of which exist on the real ResponseEventCreate schema, so the comment
    text was silently dropped. This asserts the actual outgoing request body
    uses the real field names (raw_excerpt, source_channel="manual_only")."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    comment_route = respx.post(f"{BASE}/api/v2/dunning/response-events").mock(
        return_value=httpx.Response(200, json={"response_event_id": "re-1", "review_task_id": "rt-1", "case_id": "case-1"})
    )

    resp = client.post(
        "/api/invoices/case-1/comments",
        json={"comment": "Customer confirmed payment Friday"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    sent_body = json.loads(comment_route.calls.last.request.content)
    assert sent_body["raw_excerpt"] == "Customer confirmed payment Friday"
    assert sent_body["source_channel"] == "manual_only"
    assert "response_category" not in sent_body
    assert "raw_text" not in sent_body


@respx.mock
def test_add_comment_endpoint_rejects_empty_comment(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]
    comment_route = respx.post(f"{BASE}/api/v2/dunning/response-events")

    resp = client.post(
        "/api/invoices/case-1/comments",
        json={"comment": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
    assert not comment_route.called


@respx.mock
def test_get_escalation_policy_composes_stages_and_rules(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respx.get(f"{BASE}/api/v2/dunning/policies").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "pol-1", "scope_type": "global"}]})
    )
    respx.get(f"{BASE}/api/v2/dunning/policies/pol-1/versions").mock(
        return_value=httpx.Response(
            200, json={"items": [{"id": "ver-1", "is_current_published": True, "display_label": "v1"}]}
        )
    )
    respx.get(f"{BASE}/api/v2/dunning/policy-versions/ver-1/stages").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "stage-2", "stage_code": "first_notice", "stage_name": "First Notice", "sequence_order": 2, "is_terminal_stage": False},
                {"id": "stage-1", "stage_code": "reminder", "stage_name": "Reminder", "sequence_order": 1, "is_terminal_stage": False},
            ],
        )
    )
    respx.get(f"{BASE}/api/v2/dunning/policy-versions/ver-1/stage-rules").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "rule-1", "stage_id": "stage-1", "transition_rule_json": {"max_days_in_stage": 7}},
                {"id": "rule-2", "stage_id": "stage-2", "transition_rule_json": {"max_days_in_stage": 14}},
            ],
        )
    )

    resp = client.get("/api/escalation-policy", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["version_id"] == "ver-1"
    # Sorted by sequence_order even though the stages endpoint returned them out of order.
    assert [s["stage_code"] for s in body["stages"]] == ["reminder", "first_notice"]
    assert body["stages"][0]["max_days_in_stage"] == 7
    assert body["stages"][0]["stage_rule_id"] == "rule-1"


@respx.mock
def test_update_escalation_stage_merges_not_clobbers_transition_json(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respx.get(f"{BASE}/api/v2/dunning/policy-versions/ver-1/stage-rules").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "rule-1",
                    "stage_id": "stage-1",
                    "transition_rule_json": {"max_days_in_stage": 7, "require_at_least_one_action_sent": True},
                }
            ],
        )
    )
    patch_route = respx.patch(f"{BASE}/api/v2/dunning/stage-rules/rule-1").mock(
        return_value=httpx.Response(
            200,
            json={"id": "rule-1", "transition_rule_json": {"max_days_in_stage": 10, "require_at_least_one_action_sent": True}},
        )
    )

    resp = client.patch(
        "/api/escalation-policy/versions/ver-1/stage-rules/rule-1",
        json={"max_days_in_stage": 10},
        headers=headers,
    )

    assert resp.status_code == 200
    # The sibling field must have been preserved in the outgoing PATCH body,
    # not dropped -- this is the whole point of fetch-then-merge.
    sent_body = json.loads(patch_route.calls.last.request.content)
    assert sent_body["transition_rule_json"]["require_at_least_one_action_sent"] is True
    assert sent_body["transition_rule_json"]["max_days_in_stage"] == 10


@respx.mock
def test_update_escalation_stage_404s_on_unknown_rule_id(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respx.get(f"{BASE}/api/v2/dunning/policy-versions/ver-1/stage-rules").mock(
        return_value=httpx.Response(200, json=[])
    )

    resp = client.patch(
        "/api/escalation-policy/versions/ver-1/stage-rules/nonexistent",
        json={"max_days_in_stage": 10},
        headers=headers,
    )

    assert resp.status_code == 404


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
