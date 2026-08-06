"""PM / BU-finance notification emails for terminal case-state transitions.

Added 2026-08-06 (user request): once a case escalates to a human, or a
payment gets tracked, whoever's actually accountable needs to hear about
it without checking Chases -- previously the state just flipped silently.
Reuses the same mailbox.send() + real-send + ledger.append() pattern
sync_contact_changes() (invoice_sync.py) already established. Always
sends for real -- no dry-run mode.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def _resolve_bu_finance_email(project_number: Optional[str]) -> Optional[str]:
    if not project_number:
        return None
    try:
        from app.services.backend_client import get_backend_client

        backend = get_backend_client()
        for entry in await backend.list_all_project_contacts():
            if entry.get("project_number") != project_number:
                continue
            for c in entry.get("contacts") or []:
                if c.get("contact_type") == "bu_finance" and c.get("email"):
                    return c["email"]
    except Exception:
        logger.exception("notifications: failed to resolve bu_finance contact for %s", project_number)
    return None


async def _send(
    case: Dict[str, Any],
    mailbox: Any,
    ledger: Any,
    *,
    to_addrs: List[str],
    subject: str,
    body: str,
    kind: str,
) -> None:
    from app.services.email_sender import get_email_sender

    to_line = ", ".join(to_addrs)
    msg = await mailbox.send(
        case_id=case["id"],
        thread_id=case.get("subject_token") or case["id"],
        to_addr=to_line,
        from_addr="ar-agent@local.mailbox",
        subject=subject,
        body=body,
    )
    try:
        real_send_result = await get_email_sender().send(to_line, subject, f"<p>{body}</p>", [])
    except Exception as exc:  # noqa: BLE001
        real_send_result = {"success": False, "error": str(exc)}
    await ledger.append(
        case["id"],
        kind,
        {"recipients": to_addrs, "mailbox_id": msg["id"], "real_send": real_send_result},
        principles=["P4", "P12"],
    )


async def send_escalation_notice(case: Dict[str, Any], mailbox: Any, ledger: Any) -> None:
    """Tell the PM their invoice was escalated for human review and the
    agent has stopped sending anything on it."""
    pm_email = case.get("pm_email")
    if not pm_email or mailbox is None:
        return
    invoice_no = case.get("invoice_no") or case.get("case_id")
    reason = (case.get("escalation") or {}).get("reason") or "requires human review"
    subject = f"[{case.get('subject_token')}] Invoice {invoice_no} — escalated for review"
    body = (
        "Hello,\n\n"
        f"Invoice {invoice_no} has been escalated for human review, and the automated "
        f"follow-up has stopped. Reason: {reason}\n\n"
        "Please review this case and take over from here.\n\n"
        "Thank you,\nAccounts Receivable"
    )
    await _send(case, mailbox, ledger, to_addrs=[pm_email], subject=subject, body=body, kind="escalation_notice_sent")


async def send_payment_notice(case: Dict[str, Any], mailbox: Any, ledger: Any) -> None:
    """Tell the PM and BU finance a payment was tracked on this invoice, in
    one email with both addressed together -- so finance isn't hearing it
    secondhand from the PM."""
    pm_email = case.get("pm_email")
    finance_email = await _resolve_bu_finance_email(case.get("project_number"))
    to_addrs = list({addr for addr in (pm_email, finance_email) if addr})
    if not to_addrs or mailbox is None:
        return
    invoice_no = case.get("invoice_no") or case.get("case_id")
    paid_at = (case.get("world") or {}).get("paid_at") or "recently"
    subject = f"[{case.get('subject_token')}] Invoice {invoice_no} — payment tracked"
    body = (
        "Hello,\n\n"
        f"Payment has been tracked for invoice {invoice_no} as of {paid_at}. "
        "No further follow-up is needed on this invoice.\n\n"
        "Thank you,\nAccounts Receivable"
    )
    await _send(case, mailbox, ledger, to_addrs=to_addrs, subject=subject, body=body, kind="payment_notice_sent")
