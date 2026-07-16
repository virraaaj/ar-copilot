"""
SharePoint document source via Microsoft Graph (client-credentials flow).

Uses the pre-resolved site/drive/folder ids from config (already resolved in
the Lummus dev tenant — see SHAREPOINT_SITE_ID/DRIVE_ID/FOLDER_ID), so no
site-lookup round trip is needed on every call.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import httpx

from app.config import get_settings
from app.documents.sources.base import DocumentRef, DocumentSource

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class SharePointError(Exception):
    pass


class SharePointSource(DocumentSource):
    def __init__(self) -> None:
        s = get_settings()
        self._tenant_id = s.SHAREPOINT_TENANT_ID
        self._client_id = s.SHAREPOINT_CLIENT_ID
        self._client_secret = s.SHAREPOINT_CLIENT_SECRET
        self._drive_id = s.SHAREPOINT_DRIVE_ID
        self._folder_id = s.SHAREPOINT_FOLDER_ID
        self._token: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "SharePointSource":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def _get_token(self) -> str:
        if self._token:
            return self._token
        url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        resp = await self._client.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        if resp.status_code != 200:
            raise SharePointError(f"Graph auth failed ({resp.status_code}): {resp.text[:200]}")
        self._token = resp.json()["access_token"]
        return self._token

    async def _get(self, path: str, _retry: bool = True) -> dict:
        token = await self._get_token()
        resp = await self._client.get(f"{GRAPH_BASE}{path}", headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 401 and _retry:
            self._token = None
            return await self._get(path, _retry=False)
        if resp.status_code >= 400:
            raise SharePointError(f"Graph request failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json()

    async def list_documents(self, doc_type: Optional[str] = None) -> List[DocumentRef]:
        # doc_type isn't a native Graph filter -- SharePoint has no concept of
        # our doc_type taxonomy. Filtering by it here would require a
        # separate metadata store keyed by drive item id; deferred until
        # something actually needs it (folder-per-type is the likely fix).
        data = await self._get(f"/drives/{self._drive_id}/items/{self._folder_id}/children")
        refs = []
        for item in data.get("value", []):
            if "file" not in item:  # skip subfolders
                continue
            refs.append(
                DocumentRef(
                    source="sharepoint",
                    source_id=item["id"],
                    filename=item["name"],
                    size_bytes=item.get("size"),
                    modified_at=item.get("lastModifiedDateTime"),
                )
            )
        return refs

    async def fetch(self, source_id: str) -> bytes:
        token = await self._get_token()
        resp = await self._client.get(
            f"{GRAPH_BASE}/drives/{self._drive_id}/items/{source_id}/content",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code >= 400:
            raise SharePointError(f"Graph download failed ({resp.status_code}): {resp.text[:200]}")
        return resp.content
