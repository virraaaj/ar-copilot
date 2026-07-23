"""Tests: web Chases endpoints (added 2026-07-20, Phase C4). Same
TestClient/session pattern as test_web_channel.py; ChaseStore reads
STATE_DB_PATH from settings, which the `client` fixture already points at
a temp file."""
from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.channels.web as web_module
from app.main import app
from app.services.backend_client import BackendClient, get_backend_client
from app.services.chase_store import ChaseStore

BASE = "http://test-backend"


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DOCUMENTS_LOCAL_DIR", str(tmp_path / "docs"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://placeholder/")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "placeholder")
    monkeypatch.setenv("BACKEND_API_URL", BASE)
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", "")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    web_module._sessions.clear()

    test_backend = BackendClient(BASE, "svc@example.com", "password")
    app.dependency_overrides[get_backend_client] = lambda: test_backend

    tc = TestClient(app)
    tc.chase_db_path = str(tmp_path / "state.db")  # type: ignore[attr-defined]
    yield tc

    app.dependency_overrides.clear()


def _login(client: TestClient) -> str:
    with respx.mock:
        respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
        resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    return resp.json()["session_token"]


def test_chases_endpoints_require_session(client: TestClient) -> None:
    assert client.get("/api/chases").status_code == 401
    assert client.get("/api/chases/chase-1").status_code == 401
    assert client.post("/api/chases/chase-1/pause").status_code == 401


def test_list_chases_returns_all(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    asyncio.run(store.create("case-1", invoice_no="INV-1"))
    asyncio.run(store.create("case-2", invoice_no="INV-2"))

    resp = client.get("/api/chases", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_list_chases_filters_by_state(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))
    asyncio.run(store.update(chase_id, state="escalated"))
    asyncio.run(store.create("case-2"))

    resp = client.get("/api/chases?state=escalated", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["case_id"] == "case-1"


def test_list_chases_filters_by_case_id(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    asyncio.run(store.create("case-1", invoice_no="INV-1"))
    asyncio.run(store.create("case-2", invoice_no="INV-2"))

    resp = client.get("/api/chases?case_id=case-1", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["case_id"] == "case-1"


def test_get_chase_by_id(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1", invoice_no="INV-1"))

    resp = client.get(f"/api/chases/{chase_id}", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["invoice_no"] == "INV-1"


def test_get_chase_404_when_unknown(client: TestClient) -> None:
    token = _login(client)
    resp = client.get("/api/chases/unknown-id", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_list_chase_events(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))

    resp = client.get(f"/api/chases/{chase_id}/events", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert any(e["kind"] == "created" for e in resp.json())


def test_pause_chase(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))

    resp = client.post(f"/api/chases/{chase_id}/pause", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    chase = asyncio.run(store.get(chase_id))
    assert chase["state"] == "paused"
    events = asyncio.run(store.list_events(chase_id))
    assert any(e["kind"] == "human_action" and e["detail"]["action"] == "pause" for e in events)


def test_resume_paused_chase(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))
    asyncio.run(store.update(chase_id, state="paused", target="pm"))

    resp = client.post(f"/api/chases/{chase_id}/resume", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    chase = asyncio.run(store.get(chase_id))
    assert chase["state"] == "awaiting_pm"
    assert chase["next_action_at"] is not None


def test_resume_chase_with_no_target_goes_to_pending(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))
    asyncio.run(store.update(chase_id, state="escalated"))

    resp = client.post(f"/api/chases/{chase_id}/resume", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    chase = asyncio.run(store.get(chase_id))
    assert chase["state"] == "pending"


def test_close_chase_requires_reason(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))

    resp = client.post(f"/api/chases/{chase_id}/close", json={"reason": "  "}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 422


def test_close_chase(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))

    resp = client.post(
        f"/api/chases/{chase_id}/close", json={"reason": "Customer disputed, resolved offline."},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    chase = asyncio.run(store.get(chase_id))
    assert chase["state"] == "closed_manual"


def test_restart_chase(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))
    asyncio.run(store.update(chase_id, state="escalated", missed_count=3, nudge_count=3, target="pm"))

    resp = client.post(f"/api/chases/{chase_id}/restart", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    chase = asyncio.run(store.get(chase_id))
    assert chase["state"] == "pending"
    assert chase["missed_count"] == 0
    assert chase["nudge_count"] == 0
    assert chase["target"] is None


def test_edit_commitment(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))

    resp = client.patch(
        f"/api/chases/{chase_id}/commitment", json={"promised_date": "2099-01-01"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    chase = asyncio.run(store.get(chase_id))
    assert chase["state"] == "commitment_tracked"
    assert chase["promised_date"] == "2099-01-01"


def test_edit_commitment_rejects_past_date(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    chase_id = asyncio.run(store.create("case-1"))

    resp = client.patch(
        f"/api/chases/{chase_id}/commitment", json={"promised_date": "2020-01-01"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
