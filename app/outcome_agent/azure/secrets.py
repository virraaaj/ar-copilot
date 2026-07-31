"""Load Azure connection settings from env / app Settings, optionally Key Vault."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

log = logging.getLogger(__name__)


def _az_bin() -> Optional[str]:
    for candidate in ("az", "az.cmd", "az.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _az_secret(vault: str, name: str) -> str:
    az = _az_bin()
    if not az:
        raise FileNotFoundError(
            "Azure CLI (`az`) not found. Set DATABASE_URL / COSMOS_GREMLIN_* in .env "
            "(Azure Storage is optional and not required for the agent loop)."
        )
    out = subprocess.check_output(
        [
            az,
            "keyvault",
            "secret",
            "show",
            "--vault-name",
            vault,
            "--name",
            name,
            "--query",
            "value",
            "-o",
            "tsv",
        ],
        text=True,
        shell=False,
    )
    return out.strip()


@dataclass
class AzureMemorySettings:
    database_url: str
    gremlin_host: str
    gremlin_username: str
    gremlin_password: str
    storage_connection_string: str = ""
    gremlin_port: int = 443

    @classmethod
    def from_env_or_vault(cls, vault: Optional[str] = None, app_settings: Any = None) -> "AzureMemorySettings":
        vault = vault or os.getenv("AZURE_KEY_VAULT_NAME") or getattr(
            app_settings, "AZURE_KEY_VAULT_NAME", None
        ) or "chxaragentdev-kv"

        def from_app(attr: str, env_key: str) -> str:
            if app_settings is not None:
                val = getattr(app_settings, attr, None) or getattr(app_settings, env_key, None)
                if val:
                    return str(val).strip()
            return (os.getenv(env_key) or "").strip()

        def required(env_key: str, secret_name: str, *, app_attr: Optional[str] = None) -> str:
            val = from_app(app_attr or env_key, env_key)
            if val:
                return val
            try:
                return _az_secret(vault, secret_name)
            except Exception as exc:
                raise RuntimeError(
                    f"{env_key} is not set and Key Vault secret '{secret_name}' "
                    f"could not be loaded ({exc}). "
                    "Ask for DATABASE_URL + COSMOS_GREMLIN_HOST/USERNAME/PASSWORD "
                    "(Azure Storage is NOT required for the outcome agent)."
                ) from exc

        def optional_storage() -> str:
            val = from_app("AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_CONNECTION_STRING")
            if val:
                return val
            # Storage is optional (bodies/traces only). Never fail the agent if missing.
            try:
                return _az_secret(vault, "storage-connection-string")
            except Exception as exc:
                log.info("AZURE_STORAGE_CONNECTION_STRING not available (%s) — continuing without Blob", exc)
                return ""

        return cls(
            database_url=required("DATABASE_URL", "database-url"),
            gremlin_host=required("COSMOS_GREMLIN_HOST", "cosmos-gremlin-host"),
            gremlin_username=required("COSMOS_GREMLIN_USERNAME", "cosmos-gremlin-username"),
            gremlin_password=required("COSMOS_GREMLIN_PASSWORD", "cosmos-gremlin-password"),
            storage_connection_string=optional_storage(),
            gremlin_port=int(from_app("COSMOS_GREMLIN_PORT", "COSMOS_GREMLIN_PORT") or "443"),
        )

    def as_public_dict(self) -> dict:
        return {
            "database_url_host": self.database_url.split("@")[-1] if "@" in self.database_url else "(set)",
            "gremlin_host": self.gremlin_host,
            "gremlin_username": self.gremlin_username,
            "storage_set": bool(self.storage_connection_string),
        }


def dump_settings_public(settings: AzureMemorySettings) -> str:
    return json.dumps(settings.as_public_dict(), indent=2)
