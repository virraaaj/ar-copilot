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


def test_commitment_metric_requires_session(client: TestClient) -> None:
    assert client.get("/api/chases/commitment-metric").status_code == 401


def test_commitment_metric_route_does_not_collide_with_chase_id_route(client: TestClient) -> None:
    """/chases/commitment-metric must resolve to the metric endpoint, not
    get_chase_endpoint("commitment-metric") -- route registration order in
    web.py is what makes this work."""
    token = _login(client)

    resp = client.get("/api/chases/commitment-metric", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert "known_pct" in resp.json()


def test_commitment_metric_reflects_chase_states(client: TestClient) -> None:
    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    import asyncio

    known = asyncio.run(store.create("case-1", invoice_no="INV-1"))
    asyncio.run(store.update(known, state="commitment_tracked", target="pm"))
    unknown = asyncio.run(store.create("case-2", invoice_no="INV-2"))
    asyncio.run(store.update(unknown, state="awaiting_pm", target="pm"))

    resp = client.get("/api/chases/commitment-metric", headers={"Authorization": f"Bearer {token}"})

    body = resp.json()
    assert body["total_open"] == 2
    assert body["known"] == 1
    assert body["known_pct"] == 50.0
    assert len(body["unknown_cases"]) == 1
    assert body["unknown_cases"][0]["case_id"] == "case-2"


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


# ---- simulation clock (added 2026-07-25) -----------------------------------


def test_sim_clock_endpoints_require_session(client: TestClient) -> None:
    assert client.get("/api/sim-clock").status_code == 401
    assert client.post("/api/sim-clock/advance", json={"days": 1}).status_code == 401
    assert client.post("/api/sim-clock/reset").status_code == 401


def test_sim_clock_starts_unsimulated(client: TestClient) -> None:
    token = _login(client)

    resp = client.get("/api/sim-clock", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["is_simulated"] is False


def test_sim_clock_advance_sets_simulated_time(client: TestClient) -> None:
    import asyncio
    from datetime import datetime, timedelta, timezone

    token = _login(client)
    before = datetime.now(timezone.utc).replace(tzinfo=None)

    resp = client.post("/api/sim-clock/advance", json={"days": 5}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["is_simulated"] is True
    new_time = datetime.fromisoformat(body["now"])
    assert timedelta(days=5) <= (new_time - before) <= timedelta(days=5, minutes=1)


def test_sim_clock_advance_rejects_non_positive_days(client: TestClient) -> None:
    token = _login(client)

    resp = client.post("/api/sim-clock/advance", json={"days": 0}, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 400


def test_sim_clock_reset_clears_simulated_time(client: TestClient) -> None:
    token = _login(client)
    client.post("/api/sim-clock/advance", json={"days": 5}, headers={"Authorization": f"Bearer {token}"})

    resp = client.post("/api/sim-clock/reset", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["is_simulated"] is False


def test_run_chase_tick_endpoint_requires_session(client: TestClient) -> None:
    assert client.post("/api/chases/run-tick").status_code == 401


def test_run_chase_tick_endpoint_processes_and_returns_a_count(client: TestClient) -> None:
    token = _login(client)

    with respx.mock:
        respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": []}))
        resp = client.post("/api/chases/run-tick", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"processed": 0}


# ---- simulated tools (added 2026-07-28, spec §6.17/§11.3/§11.5) -----------


def test_simulate_payment_endpoint_requires_session(client: TestClient) -> None:
    assert client.post("/api/chases/chase-1/simulate-payment").status_code == 401


def test_simulate_payment_endpoint_404_for_unknown_chase(client: TestClient) -> None:
    token = _login(client)
    resp = client.post("/api/chases/unknown-id/simulate-payment", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_simulate_payment_endpoint_closes_the_chase(client: TestClient) -> None:
    import asyncio

    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    chase_id = asyncio.run(store.create("case-1", invoice_no="INV-1"))
    asyncio.run(store.update(chase_id, state="awaiting_customer", target="customer"))

    resp = client.post(f"/api/chases/{chase_id}/simulate-payment", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["state"] == "closed_paid"


def test_inject_reply_endpoint_requires_session(client: TestClient) -> None:
    assert client.post("/api/chases/chase-1/inject-reply", json={"text": "paid"}).status_code == 401


def test_inject_reply_endpoint_404_for_unknown_chase(client: TestClient) -> None:
    token = _login(client)
    resp = client.post(
        "/api/chases/unknown-id/inject-reply", json={"text": "paid"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 404


def test_inject_reply_endpoint_parses_and_advances_the_chase(client: TestClient, monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from app.services.chase_parser import ParsedReply

    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    chase_id = asyncio.run(store.create("case-1", invoice_no="INV-1"))
    asyncio.run(store.update(chase_id, state="awaiting_customer", target="customer"))

    async def fake_advance_chase_with_reply(chase, reply_text, llm, backend, messenger, email_sender, chase_store, project_conversation_store, settings):
        await chase_store.update(chase["id"], state="commitment_tracked")
        return ParsedReply(
            intent="commitment", confidence="high", sentiment="cooperative", requires_human_review=False,
        )

    import app.services.chase_engine as chase_engine_module
    monkeypatch.setattr(chase_engine_module, "advance_chase_with_reply", fake_advance_chase_with_reply)

    resp = client.post(
        f"/api/chases/{chase_id}/inject-reply",
        json={"text": "We'll pay next week"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "commitment"
    assert body["chase"]["state"] == "commitment_tracked"


# ---- policy config viewer + outbox (added 2026-07-28, spec §6.18) ----------


def test_policy_config_requires_session(client: TestClient) -> None:
    assert client.get("/api/policy-config").status_code == 401


def test_policy_config_reflects_real_settings(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("CHASE_MAX_NUDGES", "5")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    token = _login(client)
    resp = client.get("/api/policy-config", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["contact_frequency"]["max_nudges"] == 5
    assert "high_dollar_approval_threshold" in body["escalation_rules"]
    assert "outreach" in body["allowed_actions"]
    assert "chase_enabled" in body["channel_configuration"]


def test_outbox_requires_session(client: TestClient) -> None:
    assert client.get("/api/outbox").status_code == 401


def test_outbox_empty_when_no_messages_sent(client: TestClient) -> None:
    token = _login(client)
    resp = client.get("/api/outbox", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_outbox_reflects_sent_messages_with_recipient_resolved(client: TestClient) -> None:
    import asyncio

    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    chase_id = asyncio.run(store.create("case-1", invoice_no="INV-1"))
    asyncio.run(store.update(chase_id, pm_email="pm@example.com"))
    asyncio.run(store.add_event(chase_id, "outreach_sent", {
        "target": "pm", "kind": "outreach", "text": "Invoice INV-1 is overdue.",
        "channel": "email", "error": None, "composed": False,
        "requires_human_review": False, "evaluation_failures": [], "subject": "[TOK] Re: invoice INV-1",
    }))

    resp = client.get("/api/outbox", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["recipient"] == "pm@example.com"
    assert body[0]["subject"] == "[TOK] Re: invoice INV-1"
    assert body[0]["channel"] == "email"
    assert body[0]["dry_run"] is False


# ---- temporal knowledge graph (added 2026-07-25) ---------------------------


def test_chase_graph_requires_session(client: TestClient) -> None:
    assert client.get("/api/chases/chase-1/graph").status_code == 401


def test_chase_graph_404_for_unknown_chase(client: TestClient) -> None:
    token = _login(client)
    resp = client.get("/api/chases/unknown-id/graph", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_chase_graph_empty_for_a_chase_with_no_graph_events_yet(client: TestClient) -> None:
    import asyncio

    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    chase_id = asyncio.run(store.create("case-1", invoice_no="INV-1"))

    resp = client.get(f"/api/chases/{chase_id}/graph", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"nodes": {}, "edges": []}


def test_chase_graph_reflects_written_facts(client: TestClient) -> None:
    import asyncio
    from app.services.graph_store import GraphStore

    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    chase_id = asyncio.run(store.create("case-1", invoice_no="INV-1", project_number="PN-1"))

    graph = GraphStore(db_path=client.chase_db_path)
    asyncio.run(graph.upsert_node("project:PN-1", "Customer", label="PN-1"))
    asyncio.run(graph.upsert_node("invoice:INV-1", "Invoice", label="INV-1"))
    asyncio.run(graph.add_edge("project:PN-1", "CUSTOMER_HAS_INVOICE", "invoice:INV-1"))

    resp = client.get(f"/api/chases/{chase_id}/graph", headers={"Authorization": f"Bearer {token}"})

    body = resp.json()
    assert set(body["nodes"].keys()) == {"project:PN-1", "invoice:INV-1"}
    assert len(body["edges"]) == 1
    assert body["edges"][0]["relationship"] == "CUSTOMER_HAS_INVOICE"


# ---- outcome definition (added 2026-07-28, spec §6.1) ----------------------


def test_outcome_definition_requires_session(client: TestClient) -> None:
    assert client.get("/api/outcome-definition").status_code == 401


def test_outcome_definition_returns_spec_shape(client: TestClient) -> None:
    token = _login(client)

    resp = client.get("/api/outcome-definition", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["use_case"] == "collections"
    assert "payment_date" in body["acceptable_commitments"]
    assert "closed_paid" in body["terminal_states"]


# ---- audit explainability (added 2026-07-28, spec §6.19) -------------------


def test_chase_events_include_explanations(client: TestClient) -> None:
    import asyncio

    token = _login(client)
    store = ChaseStore(db_path=client.chase_db_path)
    chase_id = asyncio.run(store.create("case-1", invoice_no="INV-1"))
    asyncio.run(store.update(chase_id, state="escalated", target="pm"))
    asyncio.run(store.add_event(chase_id, "escalated", {"reason": "dispute"}))

    resp = client.get(f"/api/chases/{chase_id}/events", headers={"Authorization": f"Bearer {token}"})

    events = resp.json()
    created = [e for e in events if e["kind"] == "created"][0]
    escalated = [e for e in events if e["kind"] == "escalated"][0]
    assert created["explanation"] is None
    assert "dispute" in escalated["explanation"].lower()
