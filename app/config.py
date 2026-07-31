"""App configuration, backed by environment variables (.env in dev)."""
from __future__ import annotations

from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Field names match the env var names, so pydantic-settings binds them
    # directly (no per-field env= needed).
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    # ---- Azure OpenAI (the only AI provider) ----
    AZURE_OPENAI_ENDPOINT: str
    AZURE_OPENAI_KEY: str
    AZURE_OPENAI_DEPLOYMENT_NAME: str = "gpt-5-mini"
    AZURE_OPENAI_API_VERSION: str = "2024-10-21"

    # ---- Backend dunning API (points at the local UAT Docker stack by default) ----
    BACKEND_API_URL: str = "http://localhost:8001"
    BACKEND_SERVICE_EMAIL: str = "uat-test@lummus.internal"
    BACKEND_SERVICE_PASSWORD: str = "UATPassword123!"

    # ---- Access control ----
    # Seed admin(s). On first run this seeds the persistent admin store; after
    # that, admins are managed at runtime and this is ignored unless the store
    # is empty.
    ADMIN_UPNS: str = "viraj.yadav@corehelix.ai"

    # TEMPORARY (2026-07-16): only this email domain can log in at all. This
    # is a blunt, stand-in gate -- not the real access-control model (that's
    # ADMIN_UPNS / the guardrails identity.py work in PLAN.md §6). Set to ""
    # to remove the restriction entirely once something better replaces it.
    ALLOWED_EMAIL_DOMAIN: str = "corehelix.ai"

    # Local/offline Outcome Agent demo: skip Lummus credential verify on
    # /api/auth/login and issue an in-memory session. Never enable outside
    # localhost. See OUTCOME_AGENT.md "Demo path (offline, no UAT backend)".
    DEV_AUTH_BYPASS: bool = False

    # ---- Bot Framework / Teams (Phase 4) ----
    MICROSOFT_APP_ID: str = ""
    MICROSOFT_APP_PASSWORD: str = ""
    MICROSOFT_APP_TENANT_ID: str = ""

    # ---- Teams -> web redirect (added 2026-07-16) ----
    # Reminder-card buttons and chat replies link out to the web UI instead of
    # opening in-Teams forms. WEB_BASE_URL is where those links point;
    # MAGIC_LINK_SECRET signs the short-lived tokens that let a Teams user
    # land already-authenticated on the right page (see guardrails/magic_link.py).
    # The default secret is fine for local dev only -- must be overridden by a
    # real random value before this is ever exposed beyond localhost.
    WEB_BASE_URL: str = "http://localhost:8090"
    MAGIC_LINK_SECRET: str = "dev-only-insecure-magic-link-secret-change-me"
    MAGIC_LINK_TTL_SECONDS: int = 900

    # ---- Local state (audit log, conversation refs, proactive dedupe) ----
    STATE_DB_PATH: str = ".state/ar_copilot.db"

    # ---- Azure production memory (Postgres + Cosmos Gremlin) ----
    # Prefer Key Vault in scripts; env overrides for local smoke tests.
    # OUTCOME_STORE_BACKEND: sqlite | azure | auto (azure if DATABASE_URL set)
    OUTCOME_STORE_BACKEND: str = "auto"
    DATABASE_URL: str = ""
    COSMOS_GREMLIN_HOST: str = ""
    COSMOS_GREMLIN_USERNAME: str = ""
    COSMOS_GREMLIN_PASSWORD: str = ""
    COSMOS_GREMLIN_PORT: int = 443
    AZURE_KEY_VAULT_NAME: str = "chxaragentdev-kv"
    AZURE_STORAGE_CONNECTION_STRING: str = ""

    # ---- Proactive engine (Phase 5) ----
    PROACTIVE_POLL_ENABLED: bool = False
    PROACTIVE_POLL_INTERVAL_SECONDS: int = 300

    # ---- Manual follow-up emails (added 2026-07-16) ----
    # A PM-triggered "follow up with the customer" campaign sends real email
    # via Microsoft Graph /sendMail -- the same transport Lummus's own V2
    # dunning engine uses (backend/app/services/integrations/
    # graph_email_service.py), stamped with the same lineage headers/footer
    # (see services/email_lineage.py) so a reply is caught by Lummus's real
    # inbound webhook exactly like a reply to an automated dunning email --
    # no new inbound-capture mechanism needed here. This needs its own Graph
    # app registration with Mail.Send permission (a different scope than
    # the SharePoint credentials above, which only cover Files.Read.All) --
    # unset everywhere accessible, same blocker as Azure OpenAI/Teams/
    # Document Intelligence/Search: built against an EmailSender interface
    # (see services/email_sender.py) with a FakeEmailSender default so this
    # is fully testable today; GraphEmailSender drops in with no call-site
    # changes once the credential exists.
    GRAPH_MAIL_TENANT_ID: str = ""
    GRAPH_MAIL_CLIENT_ID: str = ""
    GRAPH_MAIL_CLIENT_SECRET: str = ""
    EMAIL_FROM_ADDRESS: str = ""
    FOLLOWUP_POLL_INTERVAL_SECONDS: int = 300

    # ---- Outcome Agent (OUTCOME_AGENT.md); CHASE_* names shim to OUTCOME_AGENT_* ----
    # Master kill switch -- the poller doesn't even start without this.
    # Ships default-off and dry-run-first.
    CHASE_ENABLED: bool = False
    CHASE_POLL_INTERVAL_SECONDS: int = 900
    CHASE_DRY_RUN: bool = True
    CHASE_MAX_SENDS_PER_TICK: int = 10
    CHASE_MAX_NUDGES: int = 3
    CHASE_MAX_MISSED_COMMITMENTS: int = 3
    CHASE_MAX_COMMITMENT_DAYS: int = 90
    CHASE_GRACE_DAYS: int = 2
    CHASE_PAYMENT_VERIFY_DAYS: int = 3
    CHASE_NUDGE_INTERVAL_DAYS: int = 3
    CHASE_MAX_CLARIFICATIONS: int = 1
    CHASE_MAX_POSTPONEMENTS: int = 3
    CHASE_MIN_HOURS_BETWEEN_TOUCHES: int = 72
    # Comma-separated. Non-empty -> outbound chase email only goes to these
    # addresses (UAT safety net, so "the customer" is always a test mailbox).
    CHASE_TO_ADDRESS_ALLOWLIST: str = ""
    # Phase C3 inbound email polling -- separate flag from CHASE_ENABLED
    # since it needs its own Mail.Read admin consent that Mail.Send didn't
    # (see .env's GRAPH_MAIL_* comment). Teams-side chasing works without it.
    CHASE_MAIL_POLL_ENABLED: bool = False
    CHASE_MAIL_POLL_INTERVAL_SECONDS: int = 300
    # AI features (added 2026-07-22), both default off -- when off, chase
    # engine behavior is byte-for-byte what it was before either existed
    # (deterministic templates, pure nudge/miss-count escalation).
    # CHASE_COMPOSER_ENABLED: chase_composer.py rewrites the deterministic
    # template into a more natural, context-aware message before sending
    # (guardrailed against inventing dates -- falls back to the template
    # on any failure or validation reject).
    # CHASE_SMART_ESCALATION_ENABLED: chase_trajectory.py assesses the
    # whole conversation and can escalate earlier than the nudge/miss
    # caps would -- never later, the caps remain a hard backstop either way.
    CHASE_COMPOSER_ENABLED: bool = False
    CHASE_SMART_ESCALATION_ENABLED: bool = False

    @property
    def chase_to_address_allowlist(self) -> List[str]:
        return [a.strip().lower() for a in self.CHASE_TO_ADDRESS_ALLOWLIST.split(",") if a.strip()]

    # ---- Long-Horizon Outcome Agent (replaces chase runtime core) ----
    # Kill switches default safe: disabled + dry-run. When OUTCOME_AGENT_* is
    # unset, callers may still fall back to CHASE_* via the migration shim
    # properties below for one release.
    OUTCOME_AGENT_ENABLED: bool = False
    OUTCOME_AGENT_DRY_RUN: bool = True
    OUTCOME_AGENT_POLL_INTERVAL_SECONDS: int = 900
    OUTCOME_AGENT_MAX_SENDS_PER_TICK: int = 10
    OUTCOME_AGENT_MAX_NUDGES: int = 3
    OUTCOME_AGENT_MAX_MISSED_COMMITMENTS: int = 3
    OUTCOME_AGENT_MAX_COMMITMENT_DAYS: int = 90
    OUTCOME_AGENT_GRACE_DAYS: int = 2
    OUTCOME_AGENT_PAYMENT_VERIFY_DAYS: int = 3
    OUTCOME_AGENT_NUDGE_INTERVAL_DAYS: int = 3
    OUTCOME_AGENT_MAX_POSTPONEMENTS: int = 3
    OUTCOME_AGENT_MIN_HOURS_BETWEEN_TOUCHES: int = 72
    OUTCOME_AGENT_TO_ADDRESS_ALLOWLIST: str = ""
    OUTCOME_AGENT_MAIL_POLL_ENABLED: bool = False
    OUTCOME_AGENT_MAIL_POLL_INTERVAL_SECONDS: int = 300
    OUTCOME_AGENT_COMPOSER_ENABLED: bool = False
    OUTCOME_AGENT_SMART_ESCALATION_ENABLED: bool = False
    # live = Azure OpenAI forced tools; mock = deterministic/ScriptedLLM (CI)
    OUTCOME_AGENT_LLM_MODE: str = "live"

    @property
    def outcome_agent_to_address_allowlist(self) -> List[str]:
        raw = self.OUTCOME_AGENT_TO_ADDRESS_ALLOWLIST or self.CHASE_TO_ADDRESS_ALLOWLIST
        return [a.strip().lower() for a in raw.split(",") if a.strip()]

    @property
    def outcome_agent_enabled_effective(self) -> bool:
        """Prefer OUTCOME_AGENT_ENABLED; if False by default, also honor CHASE_ENABLED."""
        if self.OUTCOME_AGENT_ENABLED:
            return True
        return bool(self.CHASE_ENABLED)

    @property
    def outcome_agent_dry_run_effective(self) -> bool:
        # Both default True; if either is explicitly False, allow send (still allowlist).
        return bool(self.OUTCOME_AGENT_DRY_RUN and self.CHASE_DRY_RUN)

    # ---- Weekly AR-health digest (added 2026-07-17) ----
    # One digest card per project per ISO week, sent to that project's
    # Teams chat -- AR health (open exposure, overdue count/amount, stage
    # mix) plus a payment-pattern projection per customer derived from
    # their own closed-case history. See services/digest_engine.py.
    DIGEST_POLL_ENABLED: bool = False
    DIGEST_POLL_INTERVAL_SECONDS: int = 3600

    # ---- Document Intelligence (Phase 1B) ----
    # SharePoint (Graph API) — real dev creds, works today.
    SHAREPOINT_TENANT_ID: str = ""
    SHAREPOINT_CLIENT_ID: str = ""
    SHAREPOINT_CLIENT_SECRET: str = ""
    SHAREPOINT_SITE_URL: str = ""
    SHAREPOINT_SITE_ID: str = ""
    SHAREPOINT_DRIVE_ID: str = ""
    SHAREPOINT_FOLDER_ID: str = ""
    SHAREPOINT_DOCUMENT_LIBRARY: str = "Documents"

    # Manual upload — local disk, no external service needed.
    DOCUMENTS_LOCAL_DIR: str = ".state/documents"

    # Azure AI Search (hybrid text+vector index) — blocked, no resource yet.
    AZURE_SEARCH_ENDPOINT: str = ""
    AZURE_SEARCH_KEY: str = ""
    AZURE_SEARCH_INDEX_NAME: str = "ar-copilot-documents"

    # Azure AI Document Intelligence (OCR for scanned PDFs) — blocked, no resource yet.
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT: str = ""
    AZURE_DOCUMENT_INTELLIGENCE_KEY: str = ""

    # Embeddings model deployment (Azure OpenAI) — blocked on the same key as chat.
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME: str = "text-embedding-3-small"

    # ---- UAT data reset (dev-only, added 2026-07-23) ----
    # Direct Postgres connection to the Lummus UAT database (bypasses the
    # HTTP API entirely -- this is the one place ar-copilot talks to that
    # DB directly, since a full-table wipe has no corresponding endpoint on
    # the Lummus side). Empty by default: the wipe endpoint refuses to run
    # without this explicitly set, so it can never accidentally point at a
    # database nobody meant to be wipeable.
    UAT_DATABASE_URL: str = ""

    # ---- Server ----
    PORT: int = 8090
    LOG_LEVEL: str = "INFO"

    @property
    def admin_upns_list(self) -> List[str]:
        return [u.strip().lower() for u in self.ADMIN_UPNS.split(",") if u.strip()]


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
