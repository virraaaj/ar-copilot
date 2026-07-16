"""
Identity → role resolution (PLAN.md §6.1). Roles: `admin` (everything),
`pm` (read tools + snooze/resume/comment/start_follow_up), `viewer`
(read-only).

Both Teams and the web chat default to `pm`, not `viewer` (changed
2026-07-16 for the web side -- see channels/web.py's /chat endpoint):
anyone who reached chat already authenticated with a real Lummus login
(or a Teams identity), and the whole point of write tools existing is
letting that person act on invoices conversationally, not just via the
dedicated web forms. `viewer` still exists for contexts where read-only
is the right default (none currently call resolve_role with it, but the
role and the registry-level enforcement both stay in place).
"""
from __future__ import annotations

from app.config import get_settings


def resolve_role(email: str, default: str = "pm") -> str:
    s = get_settings()
    if email.lower() in s.admin_upns_list:
        return "admin"
    return default
