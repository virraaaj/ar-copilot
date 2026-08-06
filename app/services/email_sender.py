"""
EmailSender — swappable interface for sending real email (added
2026-07-16, for the Teams-triggered manual follow-up feature).

FakeEmailSender records everything "sent" — what followup_engine.py's
tests assert against, and remains the default until Graph mail
credentials are configured.

GraphEmailSender (real, added 2026-07-17) sends via Microsoft Graph
/sendMail using a dedicated app registration in the corehelix.ai tenant
(GRAPH_MAIL_*), modeled on Lummus's own graph_email_service.py /
sharepoint_azure_service.py client-credentials + sendMail pattern, but
using httpx (already a project dependency) instead of msal/aiohttp to
avoid adding new dependencies for a single token-acquisition call.

Known limitation: this app/mailbox is separate from the one Lummus's own
dunning engine uses, so replies are NOT automatically caught by Lummus's
inbound webhook the way automated dunning emails are -- see .env comment
above GRAPH_MAIL_TENANT_ID.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


@dataclass
class SentEmail:
    to: str
    subject: str
    html_body: str
    headers: List[Tuple[str, str]] = field(default_factory=list)


class EmailSender(ABC):
    @abstractmethod
    async def send(self, to: str, subject: str, html_body: str, headers: List[Tuple[str, str]]) -> Dict[str, Any]: ...


class FakeEmailSender(EmailSender):
    """In-memory recorder. The default via get_email_sender() until real
    credentials exist."""

    def __init__(self) -> None:
        self.sent: List[SentEmail] = []

    async def send(self, to: str, subject: str, html_body: str, headers: List[Tuple[str, str]]) -> Dict[str, Any]:
        self.sent.append(SentEmail(to=to, subject=subject, html_body=html_body, headers=headers))
        return {"success": True, "provider": "fake"}


class GraphEmailSender(EmailSender):
    """Real implementation via Microsoft Graph /sendMail -- same transport
    shape as Lummus's own graph_email_service.py, but talking to a
    separate app registration (GRAPH_MAIL_*) in the corehelix.ai tenant.

    Token acquisition uses the OAuth2 client-credentials grant directly
    against Azure AD's v2 token endpoint (POST .../oauth2/v2.0/token with
    grant_type=client_credentials, scope=https://graph.microsoft.com/.default)
    -- the same flow msal.ConfidentialClientApplication.acquire_token_for_client
    wraps, done with plain httpx since this project already depends on it
    and a single cached bearer token doesn't need a full MSAL app.
    """

    def __init__(self) -> None:
        from app.config import get_settings

        s = get_settings()
        if not s.GRAPH_MAIL_TENANT_ID or not s.GRAPH_MAIL_CLIENT_ID or not s.GRAPH_MAIL_CLIENT_SECRET or not s.EMAIL_FROM_ADDRESS:
            raise RuntimeError(
                "GraphEmailSender requires GRAPH_MAIL_TENANT_ID, GRAPH_MAIL_CLIENT_ID, "
                "GRAPH_MAIL_CLIENT_SECRET, and EMAIL_FROM_ADDRESS. Use FakeEmailSender "
                "(the default via get_email_sender()) until all are set."
            )
        self._tenant_id = s.GRAPH_MAIL_TENANT_ID
        self._client_id = s.GRAPH_MAIL_CLIENT_ID
        self._client_secret = s.GRAPH_MAIL_CLIENT_SECRET
        self._from_address = s.EMAIL_FROM_ADDRESS
        self._token_url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    async def _get_access_token(self) -> str:
        # 5-minute safety margin, same threshold sharepoint_azure_service.py uses.
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

    async def send(self, to: str, subject: str, html_body: str, headers: List[Tuple[str, str]]) -> Dict[str, Any]:
        access_token = await self._get_access_token()

        # `to` may be a comma-joined list (added 2026-08-06, escalation/
        # payment-tracked notices) -- both recipients go in one message's
        # toRecipients rather than one address per Graph API call.
        recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
        message: Dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in recipients],
        }
        if headers:
            # Graph requires custom internetMessageHeaders names to start
            # with "x-" (case-insensitive); non-"x-" names are silently
            # dropped by Graph itself, so no need to filter here.
            message["internetMessageHeaders"] = [{"name": name, "value": value} for name, value in headers]

        url = f"https://graph.microsoft.com/v1.0/users/{self._from_address}/sendMail"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"message": message, "saveToSentItems": True},
            )

        if resp.status_code == 202:
            logger.info("Email sent via Graph to %s", to)
            return {"success": True, "provider": "graph_api"}

        error_message = f"Graph API sendMail failed. Status: {resp.status_code}, Error: {resp.text}"
        logger.error(error_message)
        return {
            "success": False,
            "error": error_message,
            "provider": "graph_api",
            "status_code": resp.status_code,
        }


_sender: Optional[EmailSender] = None


def get_email_sender() -> EmailSender:
    """FakeEmailSender today; swaps to GraphEmailSender automatically once
    the Graph mail credentials are set -- no call-site changes."""
    global _sender
    if _sender is None:
        from app.config import get_settings

        s = get_settings()
        _sender = (
            GraphEmailSender()
            if (s.GRAPH_MAIL_TENANT_ID and s.GRAPH_MAIL_CLIENT_ID and s.GRAPH_MAIL_CLIENT_SECRET and s.EMAIL_FROM_ADDRESS)
            else FakeEmailSender()
        )
    return _sender
