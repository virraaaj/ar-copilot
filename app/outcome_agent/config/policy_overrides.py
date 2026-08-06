"""Project-level and default overrides for AgentPolicy (added 2026-08-06).

Same persistence pattern as runtime_flags.py: one row per (scope, field) in
the same STATE_DB_PATH SQLite file, so overrides survive a server restart.
Resolution order for a given case is:

    project override ("project:<project_number>") > default override
    ("default") > factory default (policy_from_settings(settings), i.e.
    whatever .env/Settings says)

No row for a field at a given scope means "inherit from the next scope up" --
same "absence = no override" contract runtime_flags.py already uses.

dry_run and to_address_allowlist are deliberately excluded from
OVERRIDABLE_FIELDS -- runtime_flags.py's docstring calls these out as
"deploy-time decisions, not something a UI click should be able to change,"
and that safety boundary applies just as much here.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import aiosqlite

from app.config import get_settings
from app.outcome_agent.config.policies import AgentPolicy, policy_from_settings

DEFAULT_SCOPE = "default"


def project_scope(project_number: str) -> str:
    return f"project:{project_number}"


# field -> python type, used both for validation and for casting values
# read back out of SQLite (stored as JSON text).
OVERRIDABLE_FIELDS: Dict[str, type] = {
    "max_unanswered": int,
    "max_missed_promises": int,
    "max_postponements": int,
    "max_commitment_days": int,
    "grace_days": int,
    "payment_verify_days": int,
    "nudge_interval_days": int,
    "min_hours_between_touches": int,
    "max_sends_per_tick": int,
    "high_dollar_threshold": float,
    "composer_enabled": bool,
    "smart_escalation_enabled": bool,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS oa_policy_overrides (
    scope TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (scope, field)
);
"""


def _db_path(db_path: Optional[str] = None) -> str:
    return db_path or get_settings().STATE_DB_PATH


async def _ensure_schema(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


def _validate(field: str, value: Any) -> Any:
    if field not in OVERRIDABLE_FIELDS:
        raise ValueError(f"{field} is not an overridable policy field")
    py_type = OVERRIDABLE_FIELDS[field]
    if py_type is bool:
        return bool(value)
    if py_type is int:
        return int(value)
    if py_type is float:
        return float(value)
    return value


async def get_scope_overrides(scope: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Raw overrides actually set at this exact scope (no fallback)."""
    path = _db_path(db_path)
    await _ensure_schema(path)
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute("SELECT field, value FROM oa_policy_overrides WHERE scope = ?", (scope,))
        rows = await cursor.fetchall()
    out: Dict[str, Any] = {}
    for field, raw in rows:
        try:
            out[field] = json.loads(raw)
        except json.JSONDecodeError:
            continue
    return out


async def set_scope_override(scope: str, field: str, value: Any, db_path: Optional[str] = None) -> Any:
    value = _validate(field, value)
    path = _db_path(db_path)
    await _ensure_schema(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO oa_policy_overrides (scope, field, value) VALUES (?, ?, ?) "
            "ON CONFLICT(scope, field) DO UPDATE SET value = excluded.value",
            (scope, field, json.dumps(value)),
        )
        await db.commit()
    return value


async def clear_scope_override(scope: str, field: str, db_path: Optional[str] = None) -> None:
    """Delete an override -- the field goes back to inheriting from the
    next scope up (project falls back to default; default falls back to
    the factory/.env value)."""
    path = _db_path(db_path)
    await _ensure_schema(path)
    async with aiosqlite.connect(path) as db:
        await db.execute("DELETE FROM oa_policy_overrides WHERE scope = ? AND field = ?", (scope, field))
        await db.commit()


def _apply(policy: AgentPolicy, overrides: Dict[str, Any]) -> AgentPolicy:
    if not overrides:
        return policy
    fields = policy.__dict__.copy()
    for field, value in overrides.items():
        if field == "min_hours_between_touches":
            # AgentPolicy stores this as min_days_between_emails (already
            # divided down) -- see policy_from_settings; keep the override
            # in the same user-facing unit (hours) but apply it the same way.
            fields["min_days_between_emails"] = max(1, int(value) // 24)
        elif field in fields:
            fields[field] = value
    return AgentPolicy(**fields)


async def resolve_field(field: str, project_number: Optional[str], db_path: Optional[str] = None) -> Dict[str, Any]:
    """Where a single field's effective value actually comes from --
    used by the Settings UI to show 'inherited from default' vs
    'overridden at project level'."""
    project_overrides = await get_scope_overrides(project_scope(project_number), db_path) if project_number else {}
    if field in project_overrides:
        return {"source": "project", "value": project_overrides[field]}
    default_overrides = await get_scope_overrides(DEFAULT_SCOPE, db_path)
    if field in default_overrides:
        return {"source": "default", "value": default_overrides[field]}
    return {"source": "factory", "value": None}


async def effective_policy(
    project_number: Optional[str], settings: Any, db_path: Optional[str] = None
) -> AgentPolicy:
    """The policy a case in `project_number` should actually run under:
    factory defaults, with the 'default' scope override layered on top,
    with that project's own override layered on top of that."""
    base = policy_from_settings(settings)
    default_overrides = await get_scope_overrides(DEFAULT_SCOPE, db_path)
    policy = _apply(base, default_overrides)
    if project_number:
        project_overrides = await get_scope_overrides(project_scope(project_number), db_path)
        policy = _apply(policy, project_overrides)
    return policy
