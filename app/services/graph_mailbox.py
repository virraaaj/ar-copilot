"""
Microsoft Graph inbound mailbox reader (PLAN_AGENTIC_CHASE.md Phase C3),
for the chase engine's email-reply poller. Reads the `info@corehelix.ai`
inbox (same mailbox GraphEmailSender sends from) so a customer's reply to
a chase email can be matched back to it.

Blocked today: the GRAPH_MAIL_* app registration only has Mail.Send
(deliberately least-privilege, see email_sender.py's docstring) --
reading a mailbox needs Mail.Read (Application) *plus* an Exchange
Application Access Policy scoping the app to just this one mailbox
(app-level Mail.Read is otherwise tenant-wide). That consent hasn't been
granted yet, same blocker as Mail.Send originally was. This class is
built and tested (respx-mocked) the same way GraphEmailSender was before
its own credential existed -- swapping in once consent lands is a no-op
here, the credential check just starts passing.

Same token-acquisition shape as GraphEmailSender (client-credentials
grant against Azure AD's v2 token endpoint) -- duplicated rather than
shared because it's a handful of lines and this is a genuinely separate
concern (reading vs. sending), not because sharing was hard.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import httpx


class GraphMailboxReader:
    def __init__(self) -> None:
        from app.config import get_settings

        s = get_settings()
        if not s.GRAPH_MAIL_TENANT_ID or not s.GRAPH_MAIL_CLIENT_ID or not s.GRAPH_MAIL_CLIENT_SECRET or not s.EMAIL_FROM_ADDRESS:
            raise RuntimeError(
                "GraphMailboxReader requires GRAPH_MAIL_TENANT_ID, GRAPH_MAIL_CLIENT_ID, "
                "GRAPH_MAIL_CLIENT_SECRET, and EMAIL_FROM_ADDRESS to be set."
            )
        self._tenant_id = s.GRAPH_MAIL_TENANT_ID
        self._client_id = s.GRAPH_MAIL_CLIENT_ID
        self._client_secret = s.GRAPH_MAIL_CLIENT_SECRET
        self._mailbox = s.EMAIL_FROM_ADDRESS
        self._token_url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    async def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self._token_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                    "grant_type": "client_credentials",
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Graph token request failed: {resp.status_code} {resp.text}")

        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"Graph token response missing access_token: {payload}")

        self._access_token = token
        self._token_expires_at = time.time() + payload.get("expires_in", 3600)
        return token

    async def list_recent_messages(self, top: int = 25) -> List[Dict[str, Any]]:
        """Most recent inbox messages, newest first. Each has at least
        id/subject/bodyPreview/receivedDateTime/from."""
        access_token = await self._get_access_token()
        url = (
            f"https://graph.microsoft.com/v1.0/users/{self._mailbox}/mailFolders/inbox/messages"
            f"?$top={top}&$orderby=receivedDateTime desc"
            f"&$select=id,subject,bodyPreview,receivedDateTime,from"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code != 200:
            raise RuntimeError(f"Graph mailbox list failed: {resp.status_code} {resp.text}")
        return resp.json().get("value", [])


_reader: Optional[GraphMailboxReader] = None


def get_mailbox_reader() -> Optional[GraphMailboxReader]:
    """None if credentials aren't set -- callers (chase_engine's mail
    poller) must treat that as "nothing to do this tick", not an error."""
    global _reader
    if _reader is None:
        try:
            _reader = GraphMailboxReader()
        except RuntimeError:
            return None
    return _reader
