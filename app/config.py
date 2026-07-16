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
