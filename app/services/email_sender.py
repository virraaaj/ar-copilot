"""
EmailSender — swappable interface for sending real email (added
2026-07-16, for the Teams-triggered manual follow-up feature). Blocked on
a real Graph Mail.Send credential: checked (not assumed) that
GRAPH_MAIL_CLIENT_ID/SECRET are unset everywhere accessible, same
situation as Azure OpenAI/Teams Bot Framework/Document Intelligence/Search.

FakeEmailSender records everything "sent" — what followup_engine.py's
tests assert against. A real Graph-backed implementation drops in later
with no call-site changes, same pattern as every other blocked
integration in this project (messenger.py, azure_openai.py,
documents/index.py, documents/sources/sharepoint.py).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


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
    """Real implementation via Microsoft Graph /sendMail -- the same
    transport Lummus's own V2 dispatcher uses (backend/app/services/
    integrations/graph_email_service.py). Blocked -- no Graph app
    registration with Mail.Send provisioned yet (a different scope from
    the SharePoint credentials, which only cover Files.Read.All)."""

    def __init__(self) -> None:
        from app.config import get_settings

        s = get_settings()
        if not s.GRAPH_MAIL_CLIENT_ID or not s.GRAPH_MAIL_CLIENT_SECRET or not s.EMAIL_FROM_ADDRESS:
            raise RuntimeError(
                "GraphEmailSender requires GRAPH_MAIL_TENANT_ID, GRAPH_MAIL_CLIENT_ID, "
                "GRAPH_MAIL_CLIENT_SECRET, and EMAIL_FROM_ADDRESS. Use FakeEmailSender "
                "(the default via get_email_sender()) until all are set."
            )
        raise NotImplementedError("GraphEmailSender: implement once a Graph Mail.Send credential is provisioned.")

    # ABCMeta checks abstractness (and refuses to instantiate) BEFORE
    # __init__ runs at all -- this stub exists purely so the class is
    # concrete enough to reach the __init__ guard above. It's unreachable:
    # __init__ always raises first, on either the missing-credential path
    # or the not-yet-implemented path. Same pattern as
    # channels/teams/messenger.py's BotFrameworkMessenger.
    async def send(self, to: str, subject: str, html_body: str, headers: List[Tuple[str, str]]) -> Dict[str, Any]:
        raise NotImplementedError


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
            if (s.GRAPH_MAIL_CLIENT_ID and s.GRAPH_MAIL_CLIENT_SECRET and s.EMAIL_FROM_ADDRESS)
            else FakeEmailSender()
        )
    return _sender
