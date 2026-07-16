"""
Signed, short-lived tokens for Teams -> web redirects (added 2026-07-16).

A user who already proved their identity to Teams shouldn't have to log in
again to snooze/comment on the web. A card button (or a chat reply) carries
one of these tokens in its URL; app/channels/web.py's magic-link endpoint
verifies it and hands back a normal session scoped to whatever the token
names -- one invoice+action, or a project's invoice picker.

Deliberately not a JWT library dependency: this is a small, fixed payload
shape signed with HMAC-SHA256 over a compact pipe-delimited string, which is
plenty for a single-purpose, short-lived, server-issued-and-verified token.
Tampering (of any field, including the expiry) invalidates the signature.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Optional

from app.config import get_settings

# action is one of these four; "snooze"/"comment"/"follow_up" always carry
# invoice_id, "pick_invoice" always carries project_number instead.
VALID_ACTIONS = frozenset({"snooze", "comment", "follow_up", "pick_invoice"})


class MagicLinkError(Exception):
    """Invalid, expired, or tampered token -- always safe to surface as a
    generic 'this link is no longer valid' message, never more detail."""


@dataclass
class MagicLinkPayload:
    email: str
    action: str
    invoice_id: Optional[str] = None
    project_number: Optional[str] = None
    # Only meaningful when action == "pick_invoice": which form (snooze or
    # comment) to land on once the user picks a specific invoice from that
    # project's list. Carries the original intent through the picker step.
    next_action: Optional[str] = None


def _sign(parts: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), parts.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def create_magic_link_token(payload: MagicLinkPayload, ttl_seconds: Optional[int] = None) -> str:
    if payload.action not in VALID_ACTIONS:
        raise ValueError(f"action must be one of {sorted(VALID_ACTIONS)}, got {payload.action!r}")
    if payload.action == "pick_invoice" and not payload.project_number:
        raise ValueError("pick_invoice requires project_number")
    if payload.action == "pick_invoice" and payload.next_action not in ("snooze", "comment", "follow_up"):
        raise ValueError("pick_invoice requires next_action to be 'snooze', 'comment', or 'follow_up'")
    if payload.action in ("snooze", "comment", "follow_up") and not payload.invoice_id:
        raise ValueError(f"{payload.action} requires invoice_id")

    s = get_settings()
    expires_at = int(time.time()) + (ttl_seconds if ttl_seconds is not None else s.MAGIC_LINK_TTL_SECONDS)
    # Pipe-delimited, base64url-encoded so it's URL-safe without escaping.
    # Fields never contain "|" themselves (emails/ids/numbers don't), so no
    # escaping is needed within the payload itself.
    raw = "|".join(
        [
            payload.email,
            payload.action,
            payload.invoice_id or "",
            payload.project_number or "",
            str(expires_at),
            payload.next_action or "",
        ]
    )
    body = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    sig = _sign(body, s.MAGIC_LINK_SECRET)
    return f"{body}.{sig}"


def verify_magic_link_token(token: str) -> MagicLinkPayload:
    s = get_settings()
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        raise MagicLinkError("Malformed link.")

    expected_sig = _sign(body, s.MAGIC_LINK_SECRET)
    if not hmac.compare_digest(sig, expected_sig):
        raise MagicLinkError("This link is invalid.")

    try:
        padded = body + "=" * (-len(body) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        email, action, invoice_id, project_number, expires_at_str, next_action = raw.split("|")
    except Exception:
        raise MagicLinkError("This link is invalid.")

    if int(expires_at_str) < time.time():
        raise MagicLinkError("This link has expired.")

    return MagicLinkPayload(
        email=email,
        action=action,
        invoice_id=invoice_id or None,
        project_number=project_number or None,
        next_action=next_action or None,
    )
