"""HTTP contract: cockpit inject-reply + sim-clock/jump bind JSON bodies.

Regression for FastAPI treating nested ReplyBody/JumpBody as query params
(422 loc=["query","body"]) when models lived inside build_agent_router under
from __future__ import annotations.
"""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

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
    monkeypatch.setenv("BACKEND_API_URL", BASE)
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "")
    monkeypatch.setenv("OUTCOME_AGENT_ENABLED", "true")
    monkeypatch.setenv("OUTCOME_AGENT_DRY_RUN", "true")
    import app.config as config_module

    monkeypatch.setattr(config_module, "_settings", config_module.Settings())
    web_module._sessions.clear()

    test_backend = BackendClient(BASE, "svc@example.com", "password")
    app.dependency_overrides[get_backend_client] = lambda: test_backend

    tc = TestClient(app)
    yield tc
    app.dependency_overrides.clear()


def _login(client: TestClient) -> str:
    with respx.mock:
        respx.post(f"{BASE}/api/v1/auth/login").mock(
            return_value=httpx.Response(200, json={"access_token": "tok"})
        )
        resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    assert resp.status_code == 200
    return resp.json()["session_token"]


def test_inject_reply_and_jump_accept_json_bodies(client: TestClient) -> None:
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    reset = client.post("/api/agent/demo/reset", headers=headers)
    assert reset.status_code == 200, reset.text
    assert reset.json().get("sim_date") == "2026-07-22"

    cases = client.get("/api/agent/cases", headers=headers)
    assert cases.status_code == 200
    inv = next(c for c in cases.json() if c.get("invoice_no") == "INV-4821")
    case_id = inv["id"]

    inject = client.post(
        f"/api/agent/cases/{case_id}/inject-reply",
        headers=headers,
        json={"text": "Travel approved — check back Tuesday July 28"},
    )
    assert inject.status_code == 200, inject.text
    body = inject.json()
    assert "interpretation" in body or "loop" in body

    jump = client.post(
        "/api/sim-clock/jump",
        headers=headers,
        json={"date": "2026-07-28"},
    )
    assert jump.status_code == 200, jump.text
    j = jump.json()
    assert j.get("is_simulated") is True
    assert str(j.get("simulated_at", "")).startswith("2026-07-28")


def test_inject_reply_422_is_not_query_body_missing(client: TestClient) -> None:
    """Empty/missing JSON should fail as body validation, not query['body']."""
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/agent/demo/reset", headers=headers)
    cases = client.get("/api/agent/cases", headers=headers).json()
    case_id = cases[0]["id"]

    resp = client.post(
        f"/api/agent/cases/{case_id}/inject-reply",
        headers=headers,
        json={},
    )
    assert resp.status_code == 422
    detail = resp.json().get("detail") or []
    locs = [tuple(d.get("loc") or []) for d in detail if isinstance(d, dict)]
    assert ("query", "body") not in locs
    assert any(loc[:1] == ("body",) for loc in locs)
