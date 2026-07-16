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

    # ---- Bot Framework / Teams (Phase 4) ----
    MICROSOFT_APP_ID: str = ""
    MICROSOFT_APP_PASSWORD: str = ""
    MICROSOFT_APP_TENANT_ID: str = ""

    # ---- Local state (audit log, conversation refs, proactive dedupe) ----
    STATE_DB_PATH: str = ".state/ar_copilot.db"

    # ---- Proactive engine (Phase 5) ----
    PROACTIVE_POLL_ENABLED: bool = False
    PROACTIVE_POLL_INTERVAL_SECONDS: int = 300

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
