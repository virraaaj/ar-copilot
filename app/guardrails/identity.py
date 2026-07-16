"""
Identity → role resolution (PLAN.md §6.1). Roles: `admin` (everything),
`pm` (read tools + snooze/resume/comment), `viewer` (read-only).

Teams users default to `pm` (not `viewer`) — the whole point of Phase 4 is
letting a PM act on their own invoices from chat/cards, so a Teams identity
that isn't in ADMIN_UPNS still needs write access to exactly those three
tools. The web channel (Phase 2) still defaults to `viewer` for its shared
single-login session — that's a separate call site, unaffected by this.
"""
from __future__ import annotations

from app.config import get_settings


def resolve_role(email: str, default: str = "pm") -> str:
    s = get_settings()
    if email.lower() in s.admin_upns_list:
        return "admin"
    return default
