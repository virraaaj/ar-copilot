"""Dev-only UAT database reset (added 2026-07-23). The one place ar-copilot
talks to the Lummus UAT Postgres database directly instead of through
BackendClient's HTTP API -- there's no "wipe everything" endpoint on the
Lummus side, and this is purely a local test-data reset tool, not a feature
of the product itself.

KEEP_TABLES is deliberately small: `users` (so nobody gets logged out) and
`default_project_contacts` (the template new projects are seeded from,
requested to survive resets). `schema_migrations` is never a candidate --
it's migration bookkeeping, not data.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set

import asyncpg

KEEP_TABLES: Set[str] = {
    "users",
    "default_project_contacts",
    "schema_migrations",
    # Dunning engine reference/config, not per-invoice data (discovered
    # 2026-07-23 the hard way: wiping these left new aging-Excel uploads
    # creating invoices/aging_table_data rows fine but zero dunning_cases,
    # since case creation has no policy to assign the case to).
    "dunning_policies",
    "dunning_policy_versions",
    "dunning_policy_assignments",
    "dunning_stages",
    "dunning_stage_rules",
    "dunning_stage_channel_policies",
    "dunning_stage_recipient_policies",
    "dunning_stage_recipient_map",
    "dunning_response_rule_sets",
    "dunning_response_rules",
    "dunning_call_policies",
    "dunning_grouping_policies",
    "dunning_pause_reasons",
    "dunning_bucket_config",
    "dunning_bucket_config_rows",
}


async def wipe_uat_data(database_url: str, keep_tables: Set[str] = KEEP_TABLES) -> Dict[str, Any]:
    """Truncates every public table except `keep_tables`, CASCADE so FK-
    dependent rows in kept tables (if any) are also cleared. Returns which
    tables were wiped vs. kept, for the endpoint to report back."""
    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        all_tables = [r["tablename"] for r in rows]
        to_wipe = [t for t in all_tables if t not in keep_tables]
        kept = [t for t in all_tables if t in keep_tables]

        if to_wipe:
            table_list = ", ".join(f'"{t}"' for t in to_wipe)
            await conn.execute(f"TRUNCATE TABLE {table_list} CASCADE")

        return {"wiped_tables": sorted(to_wipe), "kept_tables": sorted(kept)}
    finally:
        await conn.close()
