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
            json={
                "items": [
                    {
                        "event_type": "reply_received",
                        "event_title": "Inbound reply received",
                        "event_summary": "Paying Friday",
                        "occurred_at": "2026-07-16T21:16:32.057179",
                    }
                ]
            },
        )
    )

    resp = client.get("/api/invoices/case-1/timeline", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()[0]["summary"] == "Paying Friday"
    assert resp.json()[0]["at"] == "2026-07-16T21:16:32.057179"


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
    assert sent_body["raw_excerpt"] == "[user@example.com] Customer confirmed payment Friday"
    assert sent_body["source_channel"] == "manual_only"
    assert "response_category" not in sent_body
    assert "raw_text" not in sent_body


@respx.mock
def test_add_comment_endpoint_prefixes_the_logged_in_user(client: TestClient) -> None:
    """The real backend has no per-comment author field this app can set
    (every write goes through one shared service account) -- the "[email]"
    prefix is the only place the actual commenting user's identity
    survives, and InvoiceDetail.tsx parses it back out for display."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "pm@corehelix.ai", "password": "pw"})
    token = login_resp.json()["session_token"]

    comment_route = respx.post(f"{BASE}/api/v2/dunning/response-events").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    client.post(
        "/api/invoices/case-1/comments",
        json={"comment": "Paying next week"},
        headers={"Authorization": f"Bearer {token}"},
    )

    sent_body = json.loads(comment_route.calls.last.request.content)
    assert sent_body["raw_excerpt"] == "[pm@corehelix.ai] Paying next week"


@respx.mock
def test_add_comment_endpoint_rejects_empty_comment(client: TestClient) -> None:
    """Regression test: the email prefix is non-empty on its own, so
    whitespace-only input must be rejected *before* prefixing -- otherwise
    the tool-level empty check never sees an empty string and this would
    silently succeed instead of 422ing."""
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
def test_snooze_invoice_endpoint_happy_path(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(
        return_value=httpx.Response(200, json={"id": "case-1", "current_stage_code": "first_notice"})
    )
    pause_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    resp = client.post(
        "/api/invoices/case-1/snooze",
        json={"reason": "dispute", "resume_date": "2026-08-01"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["action"] == "snoozed"
    assert pause_route.called


@respx.mock
def test_snooze_invoice_endpoint_backend_rejection_returns_clean_422_not_raw_500(client: TestClient) -> None:
    """Regression test for a real bug found by exercising the snooze
    endpoint live against UAT data: the backend correctly 422s "cannot
    pause a closed case", but nothing translated BackendError into an HTTP
    response outside each route's own try/except, so this leaked as a raw
    500 with a stack trace. Fixed with a global exception handler in
    main.py, mirroring the existing httpx.RequestError -> 503 handler."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(
        return_value=httpx.Response(200, json={"id": "case-1", "current_stage_code": "final_notice"})
    )
    respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause").mock(
        return_value=httpx.Response(422, json={"detail": "cannot pause a closed case (status=closed_paid)"})
    )

    resp = client.post(
        "/api/invoices/case-1/snooze",
        json={"reason": "dispute"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
    assert "cannot pause a closed case" in resp.json()["detail"]


@respx.mock
def test_snooze_invoice_endpoint_refuses_pre_due_with_422(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v2/dunning/cases/case-1").mock(
        return_value=httpx.Response(200, json={"id": "case-1", "current_stage_code": "S0_pre_due"})
    )
    pause_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/pause")

    resp = client.post(
        "/api/invoices/case-1/snooze",
        json={"reason": "dispute"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422
    assert not pause_route.called


@respx.mock
def test_resume_invoice_endpoint(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    resume_route = respx.post(f"{BASE}/api/v2/dunning/cases/case-1/resume").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    resp = client.post("/api/invoices/case-1/resume", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["action"] == "resumed"
    assert resume_route.called


def test_resume_invoice_endpoint_requires_session(client: TestClient) -> None:
    resp = client.post("/api/invoices/case-1/resume")
    assert resp.status_code == 401


@respx.mock
def test_get_follow_up_status_when_none_active(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    resp = client.get("/api/invoices/case-1/follow-up", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json() == {"active_campaign": None, "send_history": []}


@respx.mock
def test_create_follow_up_happy_path(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "pm@corehelix.ai", "password": "pw"})
    token = login_resp.json()["session_token"]

    resp = client.post(
        "/api/invoices/case-1/follow-up",
        json={"customer_email": "customer@example.com", "cadence_days": 3},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert resp.json()["campaign_id"]

    status_resp = client.get("/api/invoices/case-1/follow-up", headers={"Authorization": f"Bearer {token}"})
    assert status_resp.json()["active_campaign"]["customer_email"] == "customer@example.com"
    assert status_resp.json()["active_campaign"]["requested_by"] == "pm@corehelix.ai"


@respx.mock
def test_create_follow_up_rejects_invalid_email(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    resp = client.post(
        "/api/invoices/case-1/follow-up",
        json={"customer_email": "not-an-email", "cadence_days": 3},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422


@respx.mock
def test_create_follow_up_rejects_zero_cadence(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    resp = client.post(
        "/api/invoices/case-1/follow-up",
        json={"customer_email": "customer@example.com", "cadence_days": 0},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422


@respx.mock
def test_create_follow_up_rejects_past_end_date(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    resp = client.post(
        "/api/invoices/case-1/follow-up",
        json={"customer_email": "customer@example.com", "cadence_days": 3, "end_date": "2020-01-01"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422


@respx.mock
def test_create_follow_up_rejects_second_active_campaign(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/invoices/case-1/follow-up", json={"customer_email": "a@example.com", "cadence_days": 3}, headers=headers)
    resp = client.post("/api/invoices/case-1/follow-up", json={"customer_email": "b@example.com", "cadence_days": 3}, headers=headers)

    assert resp.status_code == 409


@respx.mock
def test_cancel_follow_up_happy_path(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/api/invoices/case-1/follow-up", json={"customer_email": "a@example.com", "cadence_days": 3}, headers=headers)
    resp = client.post("/api/invoices/case-1/follow-up/cancel", headers=headers)

    assert resp.status_code == 200
    status_resp = client.get("/api/invoices/case-1/follow-up", headers=headers)
    assert status_resp.json()["active_campaign"] is None


@respx.mock
def test_cancel_follow_up_404s_when_none_active(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    resp = client.post("/api/invoices/case-1/follow-up/cancel", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 404


@respx.mock
def test_list_project_invoices_endpoint(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    cases_route = respx.get(f"{BASE}/api/v2/dunning/cases").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "case-1", "project_name": "Meridian Bay", "project_number": "PN-1"}]})
    )

    resp = client.get("/api/projects/PN-1/invoices", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()[0]["project_name"] == "Meridian Bay"
    assert cases_route.calls.last.request.url.params["project_id"] == "PN-1"


@respx.mock
def test_list_business_units_endpoint(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v1/dunning/business-units").mock(
        return_value=httpx.Response(200, json={"items": [{"bu_id": "201", "bu_name": "BU 201"}]})
    )

    resp = client.get("/api/business-units", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()[0]["bu_name"] == "BU 201"


@respx.mock
def test_list_project_contacts_endpoint(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v1/dunning/project-contacts").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "project_number": "PN-1",
                    "project_name": "Meridian Bay",
                    "contacts": [{"contact_id": "c-1", "contact_type": "pm", "name": "Jane", "email": "jane@x.com", "phone": None}],
                }
            ],
        )
    )

    resp = client.get("/api/project-contacts", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()[0]["project_number"] == "PN-1"
    assert resp.json()[0]["contacts"][0]["contact_type"] == "pm"


@respx.mock
def test_add_project_contact_endpoint(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    add_route = respx.post(f"{BASE}/api/v1/dunning/project-contacts").mock(
        return_value=httpx.Response(200, json={"contact_id": "c-1"})
    )

    resp = client.post(
        "/api/project-contacts",
        json={"project_number": "PN-1", "contact_type": "pm", "name": "Jane", "email": "jane@x.com"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    assert add_route.called


@respx.mock
def test_update_project_contact_endpoint_only_sends_provided_fields(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    patch_route = respx.patch(f"{BASE}/api/v1/dunning/project-contacts/c-1").mock(
        return_value=httpx.Response(200, json={"contact_id": "c-1", "name": "Jane Updated"})
    )

    resp = client.patch(
        "/api/project-contacts/c-1",
        json={"name": "Jane Updated"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    sent_body = json.loads(patch_route.calls.last.request.content)
    assert sent_body == {"name": "Jane Updated"}


@respx.mock
def test_delete_project_contact_endpoint(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    delete_route = respx.delete(f"{BASE}/api/v1/dunning/project-contacts/c-1").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    resp = client.delete("/api/project-contacts/c-1", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert delete_route.called


@respx.mock
def test_list_default_project_contacts_endpoint(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v1/dunning/default-project-contacts").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"bu": None, "bu_name": None, "contacts": [{"contact_id": "c-1", "contact_type": "pm", "name": "Global PM", "email": None, "phone": None}]},
            ],
        )
    )

    resp = client.get("/api/default-project-contacts", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()[0]["bu"] is None
    assert resp.json()[0]["contacts"][0]["name"] == "Global PM"


@respx.mock
def test_add_default_project_contact_endpoint_with_bu_override(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    add_route = respx.post(f"{BASE}/api/v1/dunning/default-project-contacts").mock(
        return_value=httpx.Response(201, json={"contact_id": "c-2", "bu": "201"})
    )

    resp = client.post(
        "/api/default-project-contacts",
        json={"contact_type": "pm", "bu": "201", "name": "BU 201 PM"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    sent_body = json.loads(add_route.calls.last.request.content)
    assert sent_body["bu"] == "201"


@respx.mock
def test_delete_default_project_contact_endpoint(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    delete_route = respx.delete(f"{BASE}/api/v1/dunning/default-project-contacts/c-1").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    resp = client.delete("/api/default-project-contacts/c-1", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert delete_route.called


@respx.mock
def test_magic_link_exchange_snooze_action_redirects_to_invoice_with_action_param(client: TestClient) -> None:
    from app.guardrails.magic_link import MagicLinkPayload, create_magic_link_token

    token = create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="snooze", invoice_id="case-1"))

    resp = client.post("/api/auth/magic-link", json={"token": token})

    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "pm@corehelix.ai"
    assert body["redirect"] == "/invoices/case-1?action=snooze"
    assert body["session_token"]


@respx.mock
def test_magic_link_exchange_session_token_actually_works(client: TestClient) -> None:
    """The session handed back must be usable, not just shaped correctly."""
    from app.guardrails.magic_link import MagicLinkPayload, create_magic_link_token

    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))

    token = create_magic_link_token(MagicLinkPayload(email="pm@corehelix.ai", action="comment", invoice_id="case-1"))
    exchange_resp = client.post("/api/auth/magic-link", json={"token": token})
    session_token = exchange_resp.json()["session_token"]

    respx.get(f"{BASE}/api/v2/dunning/cases/case-1/timeline").mock(return_value=httpx.Response(200, json={"items": []}))

    resp = client.get("/api/invoices/case-1/timeline", headers={"Authorization": f"Bearer {session_token}"})

    assert resp.status_code == 200


@respx.mock
def test_magic_link_exchange_pick_invoice_redirects_to_project_picker(client: TestClient) -> None:
    from app.guardrails.magic_link import MagicLinkPayload, create_magic_link_token

    token = create_magic_link_token(
        MagicLinkPayload(email="pm@corehelix.ai", action="pick_invoice", project_number="PN-1", next_action="comment")
    )

    resp = client.post("/api/auth/magic-link", json={"token": token})

    assert resp.status_code == 200
    assert resp.json()["redirect"] == "/projects/PN-1/pick-invoice?action=comment"


def test_magic_link_exchange_rejects_invalid_token(client: TestClient) -> None:
    resp = client.post("/api/auth/magic-link", json={"token": "not-a-real-token"})

    assert resp.status_code == 401


def test_magic_link_exchange_rejects_expired_token(client: TestClient) -> None:
    from app.guardrails.magic_link import MagicLinkPayload, create_magic_link_token

    token = create_magic_link_token(
        MagicLinkPayload(email="pm@corehelix.ai", action="snooze", invoice_id="case-1"), ttl_seconds=-1
    )

    resp = client.post("/api/auth/magic-link", json={"token": token})

    assert resp.status_code == 401


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
        params={"doc_type": "equipment_manual", "project_number": "PN-1"},
        headers=headers,
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["chunks_indexed"] >= 1
    assert upload_resp.json()["pages_needing_ocr"] == 0
    assert upload_resp.json()["project_number"] == "PN-1"

    search_resp = client.get("/api/documents/search", params={"query": "torque bolt A-42"}, headers=headers)
    assert search_resp.status_code == 200
    results = search_resp.json()
    assert len(results) >= 1

    # Scoped to a different project -- must not find it.
    scoped_resp = client.get(
        "/api/documents/search", params={"query": "torque bolt A-42", "project_number": "PN-2"}, headers=headers
    )
    assert scoped_resp.json() == []

    # Scoped to the right project -- finds it.
    same_project_resp = client.get(
        "/api/documents/search", params={"query": "torque bolt A-42", "project_number": "PN-1"}, headers=headers
    )
    assert len(same_project_resp.json()) >= 1


def test_upload_document_requires_project_number(client: TestClient) -> None:
    respx_router = respx.mock
    with respx_router:
        respx_router.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
        login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    pdf_bytes = make_pdf_bytes("Some content.")
    resp = client.post(
        "/api/documents/upload",
        files={"file": ("spec.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@respx.mock
def test_aging_upload_syncs_and_ticks(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respx.post(f"{BASE}/api/v1/dunning/aging-table/sync").mock(
        return_value=httpx.Response(
            200,
            json={"summary": {"inserted": 2, "updated": 1, "marked_as_paid": 0}, "dryRun": False},
        )
    )
    respx.post(f"{BASE}/api/v1/test/trigger-tick").mock(return_value=httpx.Response(200, json={"ticked": True}))

    resp = client.post(
        "/api/aging-upload",
        files={"file": ("aging.xlsx", io.BytesIO(b"fake-excel-bytes"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sync"]["summary"]["inserted"] == 2
    assert body["tick"] == {"ticked": True}
    assert body["tick_error"] is None


@respx.mock
def test_aging_upload_reports_partial_success_when_tick_fails(client: TestClient) -> None:
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]
    headers = {"Authorization": f"Bearer {token}"}

    respx.post(f"{BASE}/api/v1/dunning/aging-table/sync").mock(
        return_value=httpx.Response(200, json={"summary": {"inserted": 1, "updated": 0, "marked_as_paid": 0}})
    )
    respx.post(f"{BASE}/api/v1/test/trigger-tick").mock(return_value=httpx.Response(403, json={"detail": "test mode disabled"}))

    resp = client.post(
        "/api/aging-upload",
        files={"file": ("aging.xlsx", io.BytesIO(b"fake-excel-bytes"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["sync"]["summary"]["inserted"] == 1
    assert body["tick"] is None
    assert body["tick_error"] is not None


def test_aging_upload_requires_session(client: TestClient) -> None:
    resp = client.post(
        "/api/aging-upload",
        files={"file": ("aging.xlsx", io.BytesIO(b"fake"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 401


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
            json={"message": "any overdue invoices?", "project": {"project_number": "PN-1", "project_name": "Meridian Bay"}},
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
def test_chat_can_execute_a_write_tool(client: TestClient) -> None:
    """Regression test for a real gap found live: web chat used to hardcode
    role="viewer", so add_comment/snooze_invoice/etc were never in the
    model's tool list no matter who was logged in -- every write request
    silently had nothing to call. Now any logged-in user resolves to at
    least "pm" (see guardrails/identity.py), matching Teams."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    comment_route = respx.post(f"{BASE}/api/v2/dunning/response-events").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

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
            make_message(tool_calls=[make_tool_call("t1", "add_comment", {"invoice_id": "case-1", "comment": "Paying next week"})]),
            make_message(content="Done -- I've logged that comment."),
        ]
    )
    import app.channels.web as web_module

    original_get_llm = web_module.get_llm
    web_module.get_llm = lambda: scripted
    try:
        resp = client.post(
            "/api/chat",
            json={
                "message": "add a comment saying they're paying next week",
                "project": {"project_number": "PN-1", "project_name": "Meridian Bay"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        web_module.get_llm = original_get_llm

    events = [json.loads(line[len("data: "):]) for line in resp.text.splitlines() if line.startswith("data: ")]
    assert events[0] == {"type": "tool_call", "name": "add_comment", "permitted": True}
    assert comment_route.called


@respx.mock
def test_chat_threads_conversation_history_to_the_model(client: TestClient) -> None:
    """Regression test: the frontend now sends prior turns as `history` so
    a follow-up message ("paying next week") can be understood in the
    context of an earlier question ("what would you like the comment to
    say?") -- verifies the server actually forwards that history into the
    messages the model sees, not just accepts and drops it."""
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
    login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    captured = {}

    def make_message(content=None, tool_calls=None):
        return SimpleNamespace(content=content, tool_calls=tool_calls)

    class CapturingLLM:
        async def chat(self, messages, tools=None, tool_choice="auto"):
            captured["messages"] = messages
            return make_message(content="ok")

    import app.channels.web as web_module

    original_get_llm = web_module.get_llm
    web_module.get_llm = lambda: CapturingLLM()
    try:
        client.post(
            "/api/chat",
            json={
                "message": "paying next week",
                "project": {"project_number": "PN-1", "project_name": "Meridian Bay"},
                "history": [
                    {"role": "user", "content": "I want to add a comment"},
                    {"role": "assistant", "content": "Sure -- what would you like the comment to say?"},
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        web_module.get_llm = original_get_llm

    joined = json.dumps(captured["messages"])
    assert "I want to add a comment" in joined
    assert "what would you like the comment to say" in joined
    assert "paying next week" in joined


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
                "project": {"project_number": "PN-1", "project_name": "Meridian Bay"},
                "pinned_invoice": {"invoice_id": "case-42", "label": "Meridian Bay -- $1.25M, 21 days overdue"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        web_module.get_llm = original_get_llm

    joined = json.dumps(captured_messages["messages"])
    assert "case-42" in joined


@respx.mock
def test_chat_scopes_the_conversation_to_the_given_project(client: TestClient) -> None:
    """Project-scoped chat (added 2026-07-23): the project rides along in
    history, same mechanism as the pinned-invoice message above."""
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
                "message": "what's outstanding here?",
                "project": {"project_number": "PN-7", "project_name": "Falcon Ridge"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
    finally:
        web_module.get_llm = original_get_llm

    joined = json.dumps(captured_messages["messages"])
    assert "PN-7" in joined
    assert "Falcon Ridge" in joined


def test_chat_requires_project(client: TestClient) -> None:
    with respx.mock:
        respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
        login_resp = client.post("/api/auth/login", json={"email": "user@example.com", "password": "pw"})
    token = login_resp.json()["session_token"]

    resp = client.post(
        "/api/chat",
        json={"message": "any overdue invoices?"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# UAT data reset (added 2026-07-23)


def _login_as(client: TestClient, email: str) -> str:
    with respx.mock:
        respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))
        resp = client.post("/api/auth/login", json={"email": email, "password": "pw"})
    return resp.json()["session_token"]


def test_wipe_uat_data_requires_session(client: TestClient) -> None:
    resp = client.post("/api/admin/wipe-uat-data", json={"confirm": "WIPE_UAT_DATA"})
    assert resp.status_code == 401


def test_wipe_uat_data_requires_admin(client: TestClient) -> None:
    token = _login_as(client, "not-admin@example.com")

    resp = client.post(
        "/api/admin/wipe-uat-data",
        json={"confirm": "WIPE_UAT_DATA"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 403


def test_wipe_uat_data_requires_exact_confirm_string(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_UPNS", "admin@example.com")
    monkeypatch.setenv("UAT_DATABASE_URL", "postgresql://user:pw@localhost:5433/db")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    token = _login_as(client, "admin@example.com")

    resp = client.post(
        "/api/admin/wipe-uat-data",
        json={"confirm": "yes please"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400


def test_wipe_uat_data_requires_configured_database_url(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_UPNS", "admin@example.com")
    monkeypatch.setenv("UAT_DATABASE_URL", "")
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    token = _login_as(client, "admin@example.com")

    resp = client.post(
        "/api/admin/wipe-uat-data",
        json={"confirm": "WIPE_UAT_DATA"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 400


def test_wipe_uat_data_success_clears_local_state_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("ADMIN_UPNS", "admin@example.com")
    monkeypatch.setenv("UAT_DATABASE_URL", "postgresql://user:pw@localhost:5433/db")
    state_path = tmp_path / "state.db"
    state_path.write_text("not really sqlite, just needs to exist")
    monkeypatch.setenv("STATE_DB_PATH", str(state_path))
    import app.config as config_module
    monkeypatch.setattr(config_module, "_settings", config_module.Settings())

    async def fake_wipe(database_url: str):
        assert database_url == "postgresql://user:pw@localhost:5433/db"
        return {"wiped_tables": ["invoices", "projects"], "kept_tables": ["users", "default_project_contacts"]}

    monkeypatch.setattr(web_module, "wipe_uat_data", fake_wipe)

    token = _login_as(client, "admin@example.com")

    resp = client.post(
        "/api/admin/wipe-uat-data",
        json={"confirm": "WIPE_UAT_DATA"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["wiped_tables"] == ["invoices", "projects"]
    assert body["kept_tables"] == ["users", "default_project_contacts"]
    assert body["local_state_cleared"] is True
    assert not state_path.exists()
