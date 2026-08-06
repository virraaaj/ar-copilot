"""
Microsoft Graph inbound mailbox reader (PLAN_AGENTIC_CHASE.md Phase C3),
for the chase engine's email-reply poller. Reads the EMAIL_FROM_ADDRESS
inbox (same mailbox GraphEmailSender sends from) so a customer's reply to
a chase email can be matched back to it.

Mail.Read (Application) + the Exchange Application Access Policy consent
this previously needed has been granted (confirmed live 2026-08-06 --
list_recent_messages()/list_unread() successfully read the real inbox).
The "blocked, Mail.Send-only" note that used to live here was stale: it
described the state when this class was first written, not the current
one, and nothing had re-checked it since -- which is also how
poll_agent_mailbox calling a list_unread() method that didn't exist on
this class went unnoticed; the poller was never actually run to find out.

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

    async def list_unread(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Added 2026-08-06: poll_agent_mailbox calls this method by name
        and it never existed on this class -- the mail-poll task would
        have thrown AttributeError on its very first tick even with
        CHASE_MAIL_POLL_ENABLED on and Graph permissions granted. Filters
        server-side on isRead=false so re-polling doesn't keep re-fetching
        the whole inbox; poll_agent_mailbox's own processed-mail tracking
        (CaseStore.is_mail_processed/mark_mail_processed) is the actual
        dedup, this is just to keep each poll's response small. Returns
        the shape poll_agent_mailbox expects: id/subject/from/body (it
        reads msg.get("body") or msg.get("text"), so the full body -- not
        bodyPreview's truncated snippet -- is what a reply gets
        interpreted from)."""
        access_token = await self._get_access_token()
        url = (
            f"https://graph.microsoft.com/v1.0/users/{self._mailbox}/mailFolders/inbox/messages"
            f"?$top={limit}&$orderby=receivedDateTime desc&$filter=isRead eq false"
            f"&$select=id,subject,receivedDateTime,from,body"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"})
        if resp.status_code != 200:
            raise RuntimeError(f"Graph mailbox list (unread) failed: {resp.status_code} {resp.text}")
        raw = resp.json().get("value", [])
        out: List[Dict[str, Any]] = []
        for m in raw:
            body = m.get("body") or {}
            out.append(
                {
                    "id": m.get("id"),
                    "subject": m.get("subject"),
                    "from": (m.get("from") or {}).get("emailAddress", {}).get("address"),
                    "body": body.get("content") if body.get("contentType") == "text" else _strip_html(body.get("content") or ""),
                }
            )
        return out


def _strip_html(html: str) -> str:
    """Graph returns HTML body by default; poll_agent_mailbox's downstream
    reply-interpretation works on plain text, so strip tags rather than
    feed raw markup into the LLM/regex interpreter."""
    import re

    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text.strip()


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
