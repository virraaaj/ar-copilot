"""Tests for the existing-case refresh path added to
app/outcome_agent/loop/invoice_sync.py (_refresh_existing_case).

Before this, sync_invoices_to_cases was create-only: an already-open case
was never looked at again, so a case never learned its invoice had been
paid (or partially paid) and the agent kept chasing settled invoices.
These tests cover the three behaviours of the new refresh path:

  1. A paid invoice (status "closed_paid" or open_amount <= 0) with an
     existing open case must close that case -- even though the paid
     invoice reports a zero balance, which the old code used to skip
     BEFORE ever checking whether a case existed.
  2. A partial payment (open amount drops, invoice stays open) must
     update balance_due, log a partial_payment_detected event, and send
     exactly one partial-payment notice -- re-running the same sync with
     the same numbers must not re-send it, and must not close the case.
  3. The refreshed balance must come from the aging snapshot
     (backend.list_aging_table()), never from the invoice's face amount. A
     brand-new case falls back to the invoice figure if the aging read
     fails (there is no prior balance to protect); an *existing* case's
     balance_due is instead left untouched for that pass -- see
     _open_amounts_by_invoice_no and the aging_ok plumbing through
     _refresh_existing_case.

Uses a hand-rolled fake "backend" object (only the methods invoice_sync.py
actually calls: list_aging_table / list_all_project_contacts) and
monkeypatches invoice_sync.list_invoices_tool directly, so nothing here
talks to the real Lummus backend. The notification senders are patched to
recorder functions so no real email path (get_email_sender()) is touched.
"""
from __future__ import annotations

import pytest

import app.outcome_agent.loop.invoice_sync as invoice_sync_mod
from app.outcome_agent.loop.invoice_sync import sync_invoices_to_cases
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.event_ledger import EventLedger
from tests.outcome_agent.conftest import make_settings


INVOICE_NO = "INV-2001"
CASE_ID = f"case:{INVOICE_NO}"


class FakeBackend:
    """Only implements what invoice_sync.py touches on `backend` directly."""

    def __init__(self, aging_rows=None, aging_raises=False, contacts=None):
        self._aging_rows = aging_rows or []
        self._aging_raises = aging_raises
        self._contacts = contacts or []

    async def list_aging_table(self):
        if self._aging_raises:
            raise RuntimeError("aging feed unavailable")
        return self._aging_rows

    async def list_all_project_contacts(self):
        return self._contacts


def _patch_invoices(monkeypatch, invoices):
    async def fake_list_invoices(client, limit=500):
        return invoices

    monkeypatch.setattr(invoice_sync_mod, "list_invoices_tool", fake_list_invoices)


def _patch_notice_recorders(monkeypatch):
    """Capture calls instead of hitting notifications.py's real send path
    (which itself would hit get_email_sender()/mailbox.send)."""
    payment_calls = []
    partial_calls = []

    async def fake_payment_notice(case, mailbox, ledger):
        payment_calls.append(case["id"])

    async def fake_partial_notice(case, mailbox, ledger, *, previous_open, current_open):
        partial_calls.append((case["id"], previous_open, current_open))

    monkeypatch.setattr(invoice_sync_mod, "send_payment_notice", fake_payment_notice)
    monkeypatch.setattr(invoice_sync_mod, "send_partial_payment_notice", fake_partial_notice)
    return payment_calls, partial_calls


async def _make_open_case(store: CaseStore, *, balance_due: float, invoice_no=INVOICE_NO, case_id=CASE_ID):
    world = {
        "invoice_no": invoice_no,
        "case_id": case_id,
        "balance_due": balance_due,
        "status": "open",
        "due_date": "2026-08-01",
    }
    row_id = await store.create(
        case_id,
        invoice_no=invoice_no,
        customer_name="Acme Co",
        state="overdue",
        amount=balance_due,
        world=world,
        dialogue={},
        budget={},
        commitments=[],
        blockers=[],
        # No project_number -- keeps notifications._resolve_bu_finance_email
        # from making a real get_backend_client() call.
        pm_email="pm@example.com",
        customer_email="ap@example.com",
        target="pm",
    )
    return row_id


# ---------------------------------------------------------------------------
# Behaviour 1: paid closes the case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paid_invoice_closes_existing_case(tmp_path, monkeypatch):
    db = str(tmp_path / "oa.db")
    store = CaseStore(db_path=db)
    ledger = EventLedger(db_path=db)
    row_id = await _make_open_case(store, balance_due=3270000)

    invoices = [
        {
            "invoice_no": INVOICE_NO,
            "status": "closed_paid",
            "open_amount": 0,
            "project_number": None,
            "due_date": "2026-08-01",
            "project_name": "Acme Co",
        }
    ]
    _patch_invoices(monkeypatch, invoices)
    payment_calls, partial_calls = _patch_notice_recorders(monkeypatch)
    backend = FakeBackend(aging_rows=[])  # paid invoices don't even need an aging row

    settings = make_settings(STATE_DB_PATH=db)
    result = await sync_invoices_to_cases(backend, settings=settings, db_path=db)

    assert result["refreshed"].get(INVOICE_NO) == "closed_paid"
    assert result["closed_paid_count"] == 1

    case = await store.get(row_id)
    assert case["state"] == "paid"
    assert case["world"]["status"] == "paid"
    assert case["world"]["balance_due"] == 0.0
    assert case["world"].get("paid_at")
    assert case.get("next_action_at") is None

    events = await ledger.list_for_case(row_id)
    kinds = [e.get("kind") for e in events]
    assert "payment_posted" in kinds

    assert payment_calls == [row_id]
    assert partial_calls == []


# ---------------------------------------------------------------------------
# Behaviour 2: partial payment notifies once, keeps chasing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_payment_updates_balance_and_notifies_once(tmp_path, monkeypatch):
    db = str(tmp_path / "oa.db")
    store = CaseStore(db_path=db)
    ledger = EventLedger(db_path=db)
    row_id = await _make_open_case(store, balance_due=3270000)

    invoices = [
        {
            "invoice_no": INVOICE_NO,
            "status": "open",
            "open_amount": 3270000,  # face amount as reported by the invoice endpoint
            "project_number": None,
            "due_date": "2026-08-01",
            "project_name": "Acme Co",
        }
    ]
    _patch_invoices(monkeypatch, invoices)
    payment_calls, partial_calls = _patch_notice_recorders(monkeypatch)
    backend = FakeBackend(aging_rows=[{"invoice_number": INVOICE_NO, "open_amount": 318825}])

    settings = make_settings(STATE_DB_PATH=db)

    # --- first sync: balance drops, one notice, case stays open ---
    result = await sync_invoices_to_cases(backend, settings=settings, db_path=db)
    assert result["refreshed"].get(INVOICE_NO) == "partial_payment"

    case = await store.get(row_id)
    assert case["world"]["balance_due"] == 318825
    assert case["state"] != "paid"
    assert case["world"].get("status") != "paid"

    events = await ledger.list_for_case(row_id)
    kinds = [e.get("kind") for e in events]
    assert kinds.count("partial_payment_detected") == 1
    assert payment_calls == []
    assert partial_calls == [(row_id, 3270000.0, 318825.0)]

    # --- second sync, identical numbers: must not re-notify, must not close ---
    result2 = await sync_invoices_to_cases(backend, settings=settings, db_path=db)
    assert result2["refreshed"].get(INVOICE_NO) in ("unchanged", "partial_payment_already_notified")

    case_after = await store.get(row_id)
    assert case_after["world"]["balance_due"] == 318825
    assert case_after["state"] != "paid"

    events_after = await ledger.list_for_case(row_id)
    kinds_after = [e.get("kind") for e in events_after]
    assert kinds_after.count("partial_payment_detected") == 1  # not 2
    assert partial_calls == [(row_id, 3270000.0, 318825.0)]  # still just the one call


# ---------------------------------------------------------------------------
# Behaviour 3: open balance comes from the aging feed, not the invoice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_case_balance_comes_from_aging_not_invoice_face_amount(tmp_path, monkeypatch):
    db = str(tmp_path / "oa.db")
    store = CaseStore(db_path=db)

    invoices = [
        {
            "invoice_no": INVOICE_NO,
            "status": "open",
            "open_amount": 3270000,  # invoice's face amount -- must NOT be used
            "project_number": None,
            "due_date": "2026-08-01",
            "project_name": "Acme Co",
        }
    ]
    _patch_invoices(monkeypatch, invoices)
    _patch_notice_recorders(monkeypatch)
    backend = FakeBackend(aging_rows=[{"invoice_number": INVOICE_NO, "open_amount": 318825}])

    settings = make_settings(STATE_DB_PATH=db)
    result = await sync_invoices_to_cases(backend, settings=settings, db_path=db)

    assert result["created_count"] == 1
    case = await store.get_by_invoice(INVOICE_NO)
    assert case["world"]["balance_due"] == 318825
    assert case["amount"] == 318825


@pytest.mark.asyncio
async def test_aging_read_failure_leaves_existing_balance_untouched(tmp_path, monkeypatch):
    """Fixed 2026-09-04: this used to assert the OLD fail-open behaviour --
    an aging-read failure falling back to the invoice's face amount, which
    is always >= the true balance. For an *existing* case that fallback was
    actively harmful: it took the "balance increased" branch and permanently
    inflated world["balance_due"] with nothing to ever correct it. The sync
    must now fail closed -- leave the case's balance exactly as it was, and
    surface the outage via result["aging_unavailable"] -- while still not
    raising."""
    db = str(tmp_path / "oa.db")
    store = CaseStore(db_path=db)
    row_id = await _make_open_case(store, balance_due=318825)

    invoices = [
        {
            "invoice_no": INVOICE_NO,
            "status": "open",
            "open_amount": 3270000,  # face amount -- must NOT be trusted here
            "project_number": None,
            "due_date": "2026-08-01",
            "project_name": "Acme Co",
        }
    ]
    _patch_invoices(monkeypatch, invoices)
    payment_calls, partial_calls = _patch_notice_recorders(monkeypatch)
    backend = FakeBackend(aging_raises=True)

    settings = make_settings(STATE_DB_PATH=db)
    # Must not raise.
    result = await sync_invoices_to_cases(backend, settings=settings, db_path=db)

    assert result["aging_unavailable"] is True
    assert result["refreshed"].get(INVOICE_NO) == "aging_unavailable"

    case = await store.get(row_id)
    assert case["world"]["balance_due"] == 318825  # unchanged, not the face amount
    assert case["state"] != "paid"
    assert payment_calls == []
    assert partial_calls == []


# ---------------------------------------------------------------------------
# Fix (2026-09-04): drop -> rise -> drop-back-to-the-same-figure must notify
# again. The old "partial_notice_open_amount" dedup marker suppressed this
# because it only compared against the *last notified* amount, not whether a
# notice was ever needed in between -- a genuinely new payment that happened
# to land on a previously-seen balance was silently swallowed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_payment_notifies_again_after_drop_rise_drop_back(tmp_path, monkeypatch):
    db = str(tmp_path / "oa.db")
    store = CaseStore(db_path=db)
    ledger = EventLedger(db_path=db)
    row_id = await _make_open_case(store, balance_due=1000)

    invoices = [
        {
            "invoice_no": INVOICE_NO,
            "status": "open",
            "open_amount": 1000,
            "project_number": None,
            "due_date": "2026-08-01",
            "project_name": "Acme Co",
        }
    ]
    _patch_invoices(monkeypatch, invoices)
    payment_calls, partial_calls = _patch_notice_recorders(monkeypatch)
    settings = make_settings(STATE_DB_PATH=db)

    # 1) 1000 -> 800: genuine payment, notify.
    backend = FakeBackend(aging_rows=[{"invoice_number": INVOICE_NO, "open_amount": 800}])
    result1 = await sync_invoices_to_cases(backend, settings=settings, db_path=db)
    assert result1["refreshed"].get(INVOICE_NO) == "partial_payment"

    # 2) 800 -> 900: a correction in the sheet, balance rose.
    backend = FakeBackend(aging_rows=[{"invoice_number": INVOICE_NO, "open_amount": 900}])
    result2 = await sync_invoices_to_cases(backend, settings=settings, db_path=db)
    assert result2["refreshed"].get(INVOICE_NO) == "balance_increased"

    # 3) 900 -> 800 again: a genuinely new payment landing back on a figure
    # we've already notified about once -- must still notify.
    backend = FakeBackend(aging_rows=[{"invoice_number": INVOICE_NO, "open_amount": 800}])
    result3 = await sync_invoices_to_cases(backend, settings=settings, db_path=db)
    assert result3["refreshed"].get(INVOICE_NO) == "partial_payment"

    case = await store.get(row_id)
    assert case["world"]["balance_due"] == 800

    events = await ledger.list_for_case(row_id)
    kinds = [e.get("kind") for e in events]
    assert kinds.count("partial_payment_detected") == 2  # drop, then drop-back
    assert partial_calls == [
        (row_id, 1000.0, 800.0),
        (row_id, 900.0, 800.0),
    ]
    assert payment_calls == []


# ---------------------------------------------------------------------------
# Fix (2026-09-04): compare money in integer cents, not exact float `==`.
# Two backend responses can describe the same balance with a bare float
# representation difference (classic 0.1 + 0.2 != 0.3 territory); that must
# not be read as a real change.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_float_representation_noise_does_not_look_like_a_change(tmp_path, monkeypatch):
    db = str(tmp_path / "oa.db")
    store = CaseStore(db_path=db)
    ledger = EventLedger(db_path=db)
    # 0.1 + 0.2 == 0.30000000000000004 in IEEE 754 float -- same cents (30)
    # as the plain literal 0.3, but not exactly `==` to it.
    stored_balance = 0.1 + 0.2
    assert stored_balance != 0.3  # sanity: this is genuinely float noise, not a typo
    row_id = await _make_open_case(store, balance_due=stored_balance)

    invoices = [
        {
            "invoice_no": INVOICE_NO,
            "status": "open",
            "open_amount": 0.3,
            "project_number": None,
            "due_date": "2026-08-01",
            "project_name": "Acme Co",
        }
    ]
    _patch_invoices(monkeypatch, invoices)
    payment_calls, partial_calls = _patch_notice_recorders(monkeypatch)
    backend = FakeBackend(aging_rows=[{"invoice_number": INVOICE_NO, "open_amount": 0.3}])

    settings = make_settings(STATE_DB_PATH=db)
    result = await sync_invoices_to_cases(backend, settings=settings, db_path=db)

    assert result["refreshed"].get(INVOICE_NO) == "unchanged"

    events = await ledger.list_for_case(row_id)
    kinds = [e.get("kind") for e in events]
    assert "partial_payment_detected" not in kinds
    assert payment_calls == []
    assert partial_calls == []
