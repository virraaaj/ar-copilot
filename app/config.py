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
