"""Inbound mailbox poller rebound to CaseStore + advance_case_with_reply."""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.outcome_agent.adapters.channel_email import extract_subject_token, strip_quoted_reply
from app.outcome_agent.loop.scheduler import advance_case_with_reply
from app.outcome_agent.store.case_store import CaseStore

logger = logging.getLogger(__name__)

# Bounce/NDR (non-delivery report) messages carry the original subject
# token in a subject like "Undeliverable: [AR-xxxxxx] Invoice ..." and
# come from an Exchange system address, not the actual recipient -- found
# live 2026-08-06 sitting in the real inbox from earlier contact-change
# testing against invalid addresses. Without this filter these get
# processed as if they were a genuine customer/PM reply, feeding
# "Delivery has failed to these recipients..." into the reply interpreter.
_BOUNCE_SENDER_RE = re.compile(r"^microsoftexchange|postmaster@|mailer-daemon@", re.IGNORECASE)
_BOUNCE_SUBJECT_RE = re.compile(
    r"^(undeliverable|delivery (has )?failed|delivery status notification|mail delivery failed|"
    r"non-?delivery report)\b",
    re.IGNORECASE,
)


async def poll_agent_mailbox(
    mailbox_reader: Any,
    settings: Any,
    *,
    db_path: Optional[str] = None,
) -> int:
    """Process unread mail matching AR-###### subject tokens."""
    store = CaseStore(db_path=db_path)
    handled = 0
    try:
        messages = await mailbox_reader.list_unread(limit=25)
    except Exception:
        logger.exception("mailbox list failed")
        return 0

    from_addr = (getattr(settings, "EMAIL_FROM_ADDRESS", "") or "").lower()
    for msg in messages or []:
        mid = msg.get("id") or msg.get("message_id")
        if not mid:
            continue
        if await store.is_mail_processed(mid):
            continue
        subject = msg.get("subject") or ""
        token = extract_subject_token(subject)
        if not token:
            continue
        sender = (msg.get("from") or msg.get("sender") or "").lower()
        if from_addr and from_addr in sender:
            await store.mark_mail_processed(mid)
            continue
        if _BOUNCE_SENDER_RE.search(sender) or _BOUNCE_SUBJECT_RE.search(subject.strip()):
            await store.mark_mail_processed(mid)
            continue
        case = await store.get_by_subject_token(token)
        if not case:
            continue
        body = strip_quoted_reply(msg.get("body") or msg.get("text") or "")
        try:
            await advance_case_with_reply(case["id"], body, settings, db_path=store.db_path)
            handled += 1
        except Exception:
            logger.exception("failed to advance case for mail %s", mid)
        await store.mark_mail_processed(mid, case["id"])
    return handled
