"""Tests: digest_engine.py (added 2026-07-17)."""
from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
import respx

from app.channels.teams.messenger import FakeMessenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.services.backend_client import BackendClient
from app.services.digest_engine import compute_ar_health, compute_customer_projection, send_project_digests
from app.services.digest_store import DigestStore, current_period_key

BASE = "http://test-backend"


def _mock_login():
    respx.post(f"{BASE}/api/v1/auth/login").mock(return_value=httpx.Response(200, json={"access_token": "tok"}))


# --------------------------------------------------------------- compute_ar_health


def test_ar_health_only_counts_active_cases():
    cases = [
        {"case_status": "active", "primary_invoice_open_amount": 1000, "current_stage_code": "reminder"},
        {"case_status": "closed_paid", "primary_invoice_open_amount": 5000, "current_stage_code": "reminder"},
    ]
    health = compute_ar_health(cases)
    assert health["open_invoice_count"] == 1
    assert health["total_open_amount"] == 1000


def test_ar_health_counts_overdue_by_due_date():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    cases = [
        {"case_status": "active", "primary_invoice_open_amount": 100, "primary_invoice_due_date": yesterday, "current_stage_code": "reminder"},
        {"case_status": "active", "primary_invoice_open_amount": 200, "primary_invoice_due_date": tomorrow, "current_stage_code": "reminder"},
    ]
    health = compute_ar_health(cases)
    assert health["overdue_count"] == 1
    assert health["overdue_amount"] == 100


def test_ar_health_groups_by_stage():
    cases = [
        {"case_status": "active", "primary_invoice_open_amount": 100, "current_stage_code": "reminder"},
        {"case_status": "active", "primary_invoice_open_amount": 100, "current_stage_code": "reminder"},
        {"case_status": "active", "primary_invoice_open_amount": 100, "current_stage_code": "escalation"},
    ]
    health = compute_ar_health(cases)
    assert health["by_stage"] == {"reminder": 2, "escalation": 1}


def test_ar_health_handles_empty_list():
    health = compute_ar_health([])
    assert health["open_invoice_count"] == 0
    assert health["total_open_amount"] == 0
    assert health["overdue_count"] == 0


# ------------------------------------------------------- compute_customer_projection


def test_customer_projection_no_history_returns_unknown():
    projection = compute_customer_projection([])
    assert projection["sample_size"] == 0
    assert projection["risk"] == "unknown"


def test_customer_projection_computes_average_days_late():
    closed = [
        {"primary_invoice_due_date": "2026-06-01", "updated_at": "2026-06-20T10:00:00"},  # 19 days late
        {"primary_invoice_due_date": "2026-06-01", "updated_at": "2026-06-16T10:00:00"},  # 15 days late
    ]
    projection = compute_customer_projection(closed)
    assert projection["sample_size"] == 2
    assert projection["avg_days_relative_to_due"] == 17.0
    assert projection["risk"] == "high"


def test_customer_projection_early_payment_is_low_risk():
    closed = [{"primary_invoice_due_date": "2026-06-15", "updated_at": "2026-06-10T10:00:00"}]  # 5 days early
    projection = compute_customer_projection(closed)
    assert projection["avg_days_relative_to_due"] == -5.0
    assert projection["risk"] == "low"


def test_customer_projection_skips_cases_missing_dates():
    closed = [
        {"primary_invoice_due_date": None, "updated_at": "2026-06-15T10:00:00"},
        {"primary_invoice_due_date": "2026-06-01", "updated_at": "2026-06-01T10:00:00"},  # on time
    ]
    projection = compute_customer_projection(closed)
    assert projection["sample_size"] == 1
    assert projection["avg_days_relative_to_due"] == 0.0
    assert projection["risk"] == "low"  # avg == 0 is not > 0, so it falls to the "low" branch


# --------------------------------------------------------------- send_project_digests


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
def digest_store(tmp_path) -> DigestStore:
    return DigestStore(db_path=str(tmp_path / "state.db"))


def _case(case_id="case-1", customer_id="cust-1", project_number="PN-1", status="active"):
    return {
        "id": case_id,
        "case_key": "CK-1",
        "case_status": status,
        "current_stage_code": "reminder",
        "project_number": project_number,
        "project_name": "Meridian Bay",
        "customer_id": customer_id,
        "primary_invoice_due_date": (date.today() - timedelta(days=5)).isoformat(),
        "primary_invoice_open_amount": 1000,
        "updated_at": "2026-06-01T10:00:00",
    }


@pytest.mark.asyncio
@respx.mock
async def test_sends_one_digest_per_project_with_a_known_chat(backend, messenger, project_store, digest_store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    await project_store.record("PN-1", "conv-1")

    sent = await send_project_digests(backend, messenger, project_store, digest_store)

    assert sent == 1
    assert messenger.sent[0].conversation_id == "conv-1"
    assert "Meridian Bay" in messenger.sent[0].card["body"][1]["text"]
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_does_not_resend_within_the_same_week(backend, messenger, project_store, digest_store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    await project_store.record("PN-1", "conv-1")

    first_pass = await send_project_digests(backend, messenger, project_store, digest_store)
    second_pass = await send_project_digests(backend, messenger, project_store, digest_store)

    assert first_pass == 1
    assert second_pass == 0
    assert len(messenger.sent) == 1
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_skips_project_with_no_cases(backend, messenger, project_store, digest_store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": []}))
    await project_store.record("PN-1", "conv-1")

    sent = await send_project_digests(backend, messenger, project_store, digest_store)

    assert sent == 0
    assert messenger.sent == []
    await backend.close()


@pytest.mark.asyncio
@respx.mock
async def test_includes_trend_on_second_weeks_digest(backend, messenger, project_store, digest_store):
    _mock_login()
    respx.get(f"{BASE}/api/v2/dunning/cases").mock(return_value=httpx.Response(200, json={"items": [_case()]}))
    await project_store.record("PN-1", "conv-1")
    # Simulate last week's snapshot already existing, for a different period.
    await digest_store.record_snapshot("PN-1", "2020-W01", total_open_amount=500, open_invoice_count=1, overdue_count=0)

    await send_project_digests(backend, messenger, project_store, digest_store)

    card_text = str(messenger.sent[0].card)
    assert "vs last week" in card_text
    await backend.close()
