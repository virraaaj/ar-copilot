"""Load Azure connection settings from env or Key Vault (via `az`)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


def _az_bin() -> str:
    for candidate in ("az", "az.cmd", "az.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    raise FileNotFoundError(
        "Azure CLI (`az`) not found on PATH. Export DATABASE_URL / COSMOS_GREMLIN_* "
        "or install Azure CLI."
    )


def _az_secret(vault: str, name: str) -> str:
    out = subprocess.check_output(
        [
            _az_bin(),
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
    def from_env_or_vault(cls, vault: Optional[str] = None) -> "AzureMemorySettings":
        vault = vault or os.getenv("AZURE_KEY_VAULT_NAME", "chxaragentdev-kv")

        def get(env_key: str, secret_name: str) -> str:
            val = os.getenv(env_key, "").strip()
            if val:
                return val
            return _az_secret(vault, secret_name)

        return cls(
            database_url=get("DATABASE_URL", "database-url"),
            gremlin_host=get("COSMOS_GREMLIN_HOST", "cosmos-gremlin-host"),
            gremlin_username=get("COSMOS_GREMLIN_USERNAME", "cosmos-gremlin-username"),
            gremlin_password=get("COSMOS_GREMLIN_PASSWORD", "cosmos-gremlin-password"),
            storage_connection_string=os.getenv("AZURE_STORAGE_CONNECTION_STRING")
            or _az_secret(vault, "storage-connection-string"),
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
