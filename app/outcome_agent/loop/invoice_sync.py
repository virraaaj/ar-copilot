"""Bridges real Lummus invoices into the Outcome Agent's own case store.

Local-only addition (2026-08-03): nothing on dev-sachin ever turned an
uploaded/synced Lummus invoice into an `oa_cases` row -- the only thing
that ever populated that table was the canned day0 demo seed. This module
is the missing sync so a real aging-Excel upload actually produces a
chaseable case, not just a Dashboard row.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.agent.tools_read import list_invoices as list_invoices_tool
from app.outcome_agent.runtime.stores import build_runtime_stores
from app.services.backend_client import BackendClient
from app.services import sim_clock
from app.outcome_agent.loop.notifications import (
    send_partial_payment_notice,
    send_payment_notice,
)

logger = logging.getLogger(__name__)

DEFAULT_BUDGET: Dict[str, Any] = {
    "max_unanswered": 3,
    "unanswered_used": 0,
    "max_postponements": 3,
    "postponements_used": 0,
    "max_missed_promises": 3,
    "misses_used": 0,
    "min_days_between_emails": 3,
}


def _default_goals() -> Dict[str, Any]:
    # selected_tactic was None here, which silently defeated
    # action_simulator.py's `goals.get("selected_tactic", "polite_outreach")`
    # fallback -- dict.get's default only applies when the key is *missing*,
    # not when its value is None, so primary_tactic came out as None for
    # every brand-new case. Fixed 2026-08-06 alongside adding a real
    # PM-first tactic: a case's very first outreach should go to the PM
    # asking whether they already know a payment date, not straight to the
    # customer.
    return {
        "primary_outcome": "Collect outstanding balance while preserving relationship",
        "current_objective": "establish_contact",
        "selected_tactic": "pm_awareness_check",
        "objective_rationale": "New invoice synced from Lummus; no prior contact on file.",
    }


def _resolve_contacts(project_number: Optional[str], contacts_by_project: Dict[str, List[Dict[str, Any]]]) -> tuple[Optional[str], Optional[str]]:
    """Returns (pm_email, customer_email). This UAT dataset has no distinct
    customer/AP contact -- the PM contact is the closest real "who does the
    agent write to" address, so it's used for both roles same as the old
    chase engine's project-contact-based outreach."""
    entries = contacts_by_project.get(project_number or "", [])
    pm_email = None
    finance_email = None
    for c in entries:
        if c.get("contact_type") == "pm" and c.get("email"):
            pm_email = c["email"]
        elif c.get("contact_type") == "bu_finance" and c.get("email"):
            finance_email = c["email"]
    resolved = pm_email or finance_email
    return resolved, resolved


def _initial_state_and_wake(due_date: Optional[str], today: str) -> tuple[str, Optional[str]]:
    """A brand-new case used to always start life as "overdue" regardless
    of its actual due date, so an invoice due next month got chased
    exactly as hard as one that's 90 days late. Fixed 2026-08-06 (user
    request: chase should start automatically once overdue, not before):
    not-yet-due invoices get parked in "not_due" with next_action_at set
    to their due date, so run_agent_tick's due-date sweep only promotes
    them to "overdue" (and the poller only starts sending) once that date
    actually arrives."""
    if not due_date:
        return "overdue", None
    if due_date > today:
        return "not_due", f"{due_date}T09:00:00"
    if due_date == today:
        return "due", None
    return "overdue", None


def _cents(amount: Any) -> int:
    """Round a money value to integer cents for comparison. Fixed 2026-09-04:
    balances arrive from JSON as floats, and comparing two backend responses
    with exact `==` risks a spurious changed/unchanged verdict on a bare
    representation difference (e.g. 318825.0 vs 318824.9999999998)."""
    return round(float(amount or 0) * 100)


async def _open_amounts_by_invoice_no(backend: BackendClient) -> tuple[Dict[str, float], bool]:
    """True AR open balance per invoice, from the latest aging snapshot.

    Must not come from the invoice's `amount`: Lummus keeps that as the face
    amount on purpose and never lets the open balance overwrite it, so a
    partially-paid invoice reads at full value there.

    Returns (amounts, aging_ok). aging_ok is False when the aging feed could
    not be read. Fixed 2026-09-04: this used to swallow the exception and
    return {}, and the caller then fell back to the invoice's face amount --
    which is always >= the true balance, so a transient outage took the
    "balance increased" branch in _refresh_existing_case and permanently
    inflated world["balance_due"], with nothing to ever correct it. Callers
    must now fail closed: on aging_ok=False, leave existing balances alone
    for that pass rather than trusting the face amount.
    """
    try:
        rows = await backend.list_aging_table()
    except Exception:
        logger.exception("invoice_sync: aging-table read failed, skipping balance refresh this pass")
        return {}, False
    out: Dict[str, float] = {}
    for row in rows:
        inv_no = row.get("invoice_number")
        if not inv_no:
            continue
        try:
            out[str(inv_no)] = float(row.get("open_amount") or 0)
        except (TypeError, ValueError):
            continue
    return out, True


async def _refresh_existing_case(
    case: Dict[str, Any],
    *,
    open_amount: float,
    is_paid: bool,
    aging_ok: bool,
    today: str,
    store: Any,
    ledger: Any,
    mailbox: Any,
) -> str:
    """Bring an already-open case back in line with the backend after an
    aging upload. Returns what happened, for the sync summary.

    Before this existed the sync was create-only ("existing cases are left
    untouched"), which froze a case's view of its invoice at creation time:
    an invoice could be paid in full and the agent would carry on chasing
    someone who had already paid. The paid-stop itself was never missing --
    executor.py's hard terminal and send_payment_notice have always been
    there -- nothing was refreshing the world snapshot they read.

    Paid is taken from the invoice status, which Lummus sets when the
    invoice drops out of the weekly Hubble file. It cannot be inferred from
    the aging feed, whose last row survives payment.
    """
    world = dict(case.get("world") or {})
    previous_open = float(world.get("balance_due") or 0)

    # `open_amount <= 0` is only trustworthy when it came from the aging feed
    # itself -- when aging_ok is False, open_amount is the caller's face-amount
    # fallback, which says nothing about whether the invoice is actually paid.
    # `is_paid` (from the invoice feed, not aging) still applies either way.
    if is_paid or (aging_ok and open_amount <= 0):
        if world.get("status") == "paid" or case.get("state") == "paid":
            return "already_paid"
        world["status"] = "paid"
        world["balance_due"] = 0.0
        world["paid_at"] = today
        # Closed here rather than left for the next poller tick: the upload is
        # manual and the chase must stop the moment it lands, and a case parked
        # in "not_due" may have no next_action_at to tick on at all.
        await store.update(case["id"], state="paid", next_action_at=None, world=world, amount=0.0)
        case["state"] = "paid"
        case["world"] = world
        await ledger.append(case["id"], "payment_posted", {"paid_at": today, "source": "invoice_sync"})
        await send_payment_notice(case, mailbox, ledger)
        return "closed_paid"

    if not aging_ok:
        # Fixed 2026-09-04: fail closed on a transient aging-feed outage --
        # do NOT refresh balance_due from the invoice's face amount here. The
        # face amount is always >= the true open balance, so trusting it
        # would take the "balance increased" branch below and permanently
        # inflate the chased amount, with nothing that ever corrects it.
        # Leave the case's balance exactly as it was for this pass.
        return "aging_unavailable"

    if _cents(open_amount) == _cents(previous_open):
        return "unchanged"

    world["balance_due"] = open_amount
    if _cents(open_amount) > _cents(previous_open):
        # Balance went up (re-issue, FX, a correction in the sheet). Record it,
        # but there is nothing to tell anyone about -- no money moved to us.
        await store.update(case["id"], world=world, amount=open_amount)
        return "balance_increased"

    # Balance dropped without the invoice closing == part-payment. Notified
    # every time this branch is reached; the "unchanged" check above already
    # covers a same-snapshot re-upload (balance_due already matches, so we
    # never get here), and a dedup marker here only ever fired on a
    # drop -> rise -> drop-back-to-the-same-figure sequence, where it wrongly
    # swallowed a second, genuinely new payment. Removed 2026-09-04.
    await store.update(case["id"], world=world, amount=open_amount)
    case["world"] = world
    await ledger.append(
        case["id"],
        "partial_payment_detected",
        {"previous_open": previous_open, "current_open": open_amount, "paid_delta": previous_open - open_amount},
    )
    await send_partial_payment_notice(
        case, mailbox, ledger, previous_open=previous_open, current_open=open_amount
    )
    return "partial_payment"


async def sync_invoices_to_cases(
    backend: BackendClient,
    settings=None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Fetch real invoices from Lummus, create an oa_cases row for any that
    don't have one, and bring existing cases back in line with the backend.

    Idempotent -- safe to call after every upload. Existing cases used to be
    left untouched, which meant a case never learned its invoice had been
    paid and the agent kept chasing settled invoices; see
    _refresh_existing_case. Payment is read from the backend rather than
    re-derived here: Lummus already does the weekly-snapshot diff on ingest
    (an invoice absent from the new Hubble file is marked Paid), so this
    keeps that logic in one place."""
    from app.config import get_settings

    settings = settings or get_settings()
    bundle = build_runtime_stores(settings, db_path=db_path)
    store = bundle.case
    ledger = bundle.ledger
    graph = bundle.graph
    mailbox = bundle.mailbox

    invoices = await list_invoices_tool(backend, limit=500)
    open_by_invoice_no, aging_ok = await _open_amounts_by_invoice_no(backend)
    today = (await sim_clock.now(store.db_path)).date().isoformat()

    contacts_by_project: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for entry in await backend.list_all_project_contacts():
            contacts_by_project[entry.get("project_number") or ""] = entry.get("contacts") or []
    except Exception:
        logger.exception("invoice_sync: failed to load project contacts, proceeding without emails")

    created: List[str] = []
    skipped: List[str] = []
    refreshed: Dict[str, str] = {}

    for inv in invoices:
        invoice_no = inv.get("invoice_no")
        if not invoice_no:
            continue
        # The aging snapshot carries the real AR balance; the invoice figure is
        # the face amount and only stands in when the aging feed is unavailable.
        open_amount = float(open_by_invoice_no.get(invoice_no, inv.get("open_amount") or 0))
        is_paid = inv.get("status") == "closed_paid"

        case_id = f"case:{invoice_no}"
        existing = await store.get_open_for_case(case_id)
        if existing:
            # Checked before the zero-balance skip below: a fully paid invoice
            # is exactly the case that most needs its open case closed, and
            # skipping on balance first meant it never got looked at.
            outcome = await _refresh_existing_case(
                existing,
                open_amount=open_amount,
                is_paid=is_paid,
                aging_ok=aging_ok,
                today=today,
                store=store,
                ledger=ledger,
                mailbox=mailbox,
            )
            refreshed[invoice_no] = outcome
            continue

        if is_paid or open_amount <= 0:
            skipped.append(invoice_no)
            continue

        project_number = inv.get("project_number")
        customer_name = inv.get("project_name") or project_number or invoice_no
        pm_email, customer_email = _resolve_contacts(project_number, contacts_by_project)
        initial_state, wake_at = _initial_state_and_wake(inv.get("due_date"), today)

        world = {
            "invoice_no": invoice_no,
            "case_id": case_id,
            "balance_due": open_amount,
            "status": "open",
            "due_date": inv.get("due_date"),
            "consent_ok": True,
            "internal_owner": pm_email,
            "risk": "medium",
            "customer_name": customer_name,
            "project_number": project_number,
            "amount_original": open_amount,
            "source": "backend",
        }

        row_id = await store.create(
            case_id,
            case_key=inv.get("case_key"),
            invoice_no=invoice_no,
            project_number=project_number,
            customer_name=customer_name,
            state=initial_state,
            next_action_at=wake_at,
            amount=open_amount,
            world=world,
            dialogue={},
            budget=dict(DEFAULT_BUDGET),
            goals=_default_goals(),
            commitments=[],
            blockers=[],
            pm_email=pm_email,
            customer_email=customer_email,
            # "pm" until the PM explicitly authorizes contacting the
            # customer directly or gives a distinct customer contact --
            # see signal_ingestion.py's target flip and domain/goals.py's
            # contact_target guard. Was "customer" (unused, dead field)
            # before 2026-08-06.
            target="pm",
        )
        created.append(row_id)

        await ledger.append(
            row_id,
            "invoice_synced",
            {"invoice_no": invoice_no, "open_amount": open_amount, "project_number": project_number},
            principles=["P12"],
        )

        if graph is not None:
            try:
                cust_node = f"customer:{project_number or invoice_no}"
                inv_node = f"invoice:{invoice_no}"
                await graph.upsert_node(cust_node, "Customer", label=customer_name)
                await graph.upsert_node(inv_node, "Invoice", label=invoice_no, attributes={"balance_due": open_amount})
                await graph.supersede_edge(cust_node, "CUSTOMER_HAS_INVOICE", inv_node)
            except Exception:
                logger.exception("invoice_sync: graph write failed for %s", invoice_no)

    contact_change_result = await sync_contact_changes(backend, settings=settings, db_path=db_path, bundle=bundle)

    return {
        "created": created,
        "created_count": len(created),
        "skipped_count": len(skipped),
        "refreshed": refreshed,
        "closed_paid_count": sum(1 for v in refreshed.values() if v == "closed_paid"),
        "partial_payment_count": sum(1 for v in refreshed.values() if v == "partial_payment"),
        "contact_changes": contact_change_result,
        # Surfaced 2026-09-04 so an operator can see when existing-case
        # balance refresh was skipped for this pass because the aging feed
        # could not be read (see _open_amounts_by_invoice_no).
        "aging_unavailable": not aging_ok,
    }


async def sync_contact_changes(
    backend: BackendClient,
    settings=None,
    db_path: Optional[str] = None,
    bundle=None,
) -> Dict[str, Any]:
    """For every open case that has already sent at least one email (i.e.
    the chase has genuinely been invoked -- last_outreach_at is set),
    re-resolve that project's current contact. If it's changed since the
    case was created, notify the OLD contact they're no longer needed and
    switch the case over to the new one so the chase continues normally.

    Added 2026-08-06 (user feedback): previously a project_contacts edit
    made after a chase started had zero effect on already-created cases
    -- they just kept emailing whoever was the contact when the case was
    made, with the changed-away contact never told to stop expecting to
    hear from us.
    """
    from app.config import get_settings as _get_settings
    from app.outcome_agent.domain.types import TERMINAL_STATES
    from app.services.email_sender import get_email_sender

    settings = settings or _get_settings()
    bundle = bundle or build_runtime_stores(settings, db_path=db_path)
    store = bundle.case
    mailbox = bundle.mailbox
    ledger = bundle.ledger

    contacts_by_project: Dict[str, List[Dict[str, Any]]] = {}
    try:
        for entry in await backend.list_all_project_contacts():
            contacts_by_project[entry.get("project_number") or ""] = entry.get("contacts") or []
    except Exception:
        logger.exception("sync_contact_changes: failed to load project contacts, skipping")
        return {"checked": 0, "notified": []}

    all_cases = await store.list_all()
    notified: List[Dict[str, Any]] = []
    checked = 0

    for case in all_cases:
        if case.get("state") in TERMINAL_STATES or case.get("state") == "escalated_to_human":
            continue
        if not case.get("last_outreach_at"):
            continue  # chase never actually sent anything -- nothing to notify about
        project_number = case.get("project_number")
        if not project_number:
            continue
        checked += 1

        new_pm, new_customer = _resolve_contacts(project_number, contacts_by_project)
        # Every drifted field gets updated regardless of whether its old
        # value happens to match another field's -- only the *notification
        # email* gets deduped below (one email per distinct old address,
        # not one per field), so a pm_email/customer_email pair that were
        # both pointing at the same old contact don't get two copies of
        # the same notice.
        changed: List[tuple[str, str, str]] = []
        old_pm = case.get("pm_email")
        old_customer = case.get("customer_email")
        if new_pm and old_pm and new_pm != old_pm:
            changed.append(("pm_email", old_pm, new_pm))
        if new_customer and old_customer and new_customer != old_customer:
            changed.append(("customer_email", old_customer, new_customer))
        if not changed:
            continue

        invoice_no = case.get("invoice_no") or case.get("case_id")
        subject = f"[{case.get('subject_token')}] Invoice {invoice_no} — contact update"
        body = (
            "Hello,\n\n"
            f"This is to let you know that the project contact for invoice {invoice_no} has changed. "
            "You will no longer receive updates or need to respond about this invoice going forward -- "
            "we'll be reaching out to the new contact directly.\n\n"
            "Thank you,\nAccounts Receivable"
        )
        update_fields: Dict[str, Any] = {}
        notified_addrs: set[str] = set()
        for field, old_addr, new_addr in changed:
            update_fields[field] = new_addr
            notified.append(
                {"case_id": case["id"], "invoice_no": invoice_no, "field": field, "old": old_addr, "new": new_addr}
            )
            if old_addr in notified_addrs:
                continue
            notified_addrs.add(old_addr)
            msg = await mailbox.send(
                case_id=case["id"],
                thread_id=case.get("subject_token") or case["id"],
                to_addr=old_addr,
                from_addr="ar-agent@local.mailbox",
                subject=subject,
                body=body,
            )
            try:
                real_send_result = await get_email_sender().send(old_addr, subject, f"<p>{body}</p>", [])
            except Exception as exc:  # noqa: BLE001
                real_send_result = {"success": False, "error": str(exc)}
            await ledger.append(
                case["id"],
                "contact_changed_notice",
                {
                    "fields_changed": [f for f, o, _ in changed if o == old_addr],
                    "old_contact": old_addr, "new_contact": new_addr,
                    "mailbox_id": msg["id"], "real_send": real_send_result,
                },
                principles=["P4", "P12"],
            )

        await store.update(case["id"], **update_fields)

    return {"checked": checked, "notified": notified}
