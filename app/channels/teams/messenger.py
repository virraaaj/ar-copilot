"""
TeamsMessenger — swappable interface for sending into Teams (PLAN.md §5
Phase 4).

FakeMessenger records everything sent — what bot.py and proactive.py's tests
assert against, and remains the default until Bot Framework credentials
are configured.

BotFrameworkMessenger (real, added 2026-07-17) sends via the Bot Connector
REST API (POST {serviceUrl}/v3/conversations/{id}/activities). Unlike
Azure OpenAI or Graph email, this one needs a piece of state no credential
alone provides: serviceUrl, the per-region Connector endpoint a reply must
be posted to, which is only learned from an *incoming* activity (see
channels/teams/http.py, which persists it via ProjectConversationStore).
So sending to a conversation this bot has never received a message from
yet is a hard failure, not a missing-credential one -- see
_ConversationServiceUrlUnknown below.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class SentMessage:
    conversation_id: str
    text: Optional[str] = None
    card: Optional[Dict[str, Any]] = None


class TeamsMessenger(ABC):
    @abstractmethod
    async def send_text(self, conversation_id: str, text: str) -> None: ...

    @abstractmethod
    async def send_card(self, conversation_id: str, card: Dict[str, Any]) -> None: ...

    @abstractmethod
    async def create_group_conversation(self, topic: str, member_emails: List[str]) -> str:
        """Create (or would-create) a group chat with these members and
        return its conversation_id. Added 2026-07-16 for project-level
        Teams chats: one conversation per project, seeded with that
        project's contacts, replacing the earlier 1:1-DM-per-PM model."""
        ...


class FakeMessenger(TeamsMessenger):
    """In-memory recorder. The default via get_messenger() until real
    credentials exist."""

    def __init__(self) -> None:
        self.sent: List[SentMessage] = []
        self.created_conversations: Dict[str, List[str]] = {}  # conversation_id -> members
        self._next_id = 1

    async def send_text(self, conversation_id: str, text: str) -> None:
        self.sent.append(SentMessage(conversation_id=conversation_id, text=text))

    async def send_card(self, conversation_id: str, card: Dict[str, Any]) -> None:
        self.sent.append(SentMessage(conversation_id=conversation_id, card=card))

    async def create_group_conversation(self, topic: str, member_emails: List[str]) -> str:
        conversation_id = f"fake-group-{self._next_id}"
        self._next_id += 1
        self.created_conversations[conversation_id] = list(member_emails)
        return conversation_id


class ConversationServiceUrlUnknown(RuntimeError):
    """Raised when trying to send into a conversation_id this bot has never
    received an incoming activity for (or, for create_group_conversation,
    when the bot has never received *any* activity at all yet) -- there's
    no serviceUrl to POST to. Not a credential problem; the fix is "get a
    real message into that conversation first" (e.g. sideload the Teams
    app and have someone say anything to the bot), not a config change."""


class BotFrameworkMessenger(TeamsMessenger):
    """Real implementation via the Bot Connector REST API. Token
    acquisition is the same OAuth2 client-credentials grant as
    email_sender.py's GraphEmailSender, just against a different resource
    (api.botframework.com instead of Graph)."""

    def __init__(self, service_url_store: Optional[Any] = None) -> None:
        from app.config import get_settings

        s = get_settings()
        if not s.MICROSOFT_APP_ID or not s.MICROSOFT_APP_PASSWORD or not s.MICROSOFT_APP_TENANT_ID:
            raise RuntimeError(
                "BotFrameworkMessenger requires MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD, and "
                "MICROSOFT_APP_TENANT_ID. Use FakeMessenger (the default via get_messenger()) until all are set."
            )
        self._app_id = s.MICROSOFT_APP_ID
        self._app_password = s.MICROSOFT_APP_PASSWORD
        self._tenant_id = s.MICROSOFT_APP_TENANT_ID
        self._token_url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

        if service_url_store is None:
            from app.channels.teams.project_conversation_store import ProjectConversationStore

            service_url_store = ProjectConversationStore()
        self._service_url_store = service_url_store

    async def _get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 300:
            return self._access_token

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self._token_url,
                data={
                    "client_id": self._app_id,
                    "client_secret": self._app_password,
                    "scope": "https://api.botframework.com/.default",
                    "grant_type": "client_credentials",
                },
            )
        if resp.status_code != 200:
            raise RuntimeError(f"Bot Framework token request failed: {resp.status_code} {resp.text}")

        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"Bot Framework token response missing access_token: {payload}")

        self._access_token = token
        self._token_expires_at = time.time() + payload.get("expires_in", 3600)
        return token

    async def _post_activity(self, conversation_id: str, service_url: str, activity: Dict[str, Any]) -> None:
        access_token = await self._get_access_token()
        url = f"{service_url.rstrip('/')}/v3/conversations/{conversation_id}/activities"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json=activity,
            )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(f"Bot Connector send failed: {resp.status_code} {resp.text}")

    async def send_text(self, conversation_id: str, text: str) -> None:
        service_url = await self._service_url_store.get_service_url(conversation_id)
        if not service_url:
            raise ConversationServiceUrlUnknown(
                f"No known serviceUrl for conversation {conversation_id!r} -- the bot hasn't received an "
                "incoming activity from it yet, so there's nowhere to send a reply."
            )
        await self._post_activity(conversation_id, service_url, {"type": "message", "text": text})

    async def send_card(self, conversation_id: str, card: Dict[str, Any]) -> None:
        service_url = await self._service_url_store.get_service_url(conversation_id)
        if not service_url:
            raise ConversationServiceUrlUnknown(
                f"No known serviceUrl for conversation {conversation_id!r} -- the bot hasn't received an "
                "incoming activity from it yet, so there's nowhere to send a reply."
            )
        activity = {
            "type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
        }
        await self._post_activity(conversation_id, service_url, activity)

    async def create_group_conversation(self, topic: str, member_emails: List[str]) -> str:
        """Proactively starts a brand-new Teams group chat. Reuses the most
        recently seen serviceUrl (see ProjectConversationStore.get_any_
        known_service_url's docstring for why that's the standard approach,
        not a hack) since a conversation that doesn't exist yet obviously
        has none of its own."""
        service_url = await self._service_url_store.get_any_known_service_url()
        if not service_url:
            raise ConversationServiceUrlUnknown(
                "No serviceUrl known yet for any conversation -- the bot must receive at least one real "
                "incoming activity (e.g. someone messaging it in Teams) before it can proactively start "
                "new conversations."
            )

        access_token = await self._get_access_token()
        url = f"{service_url.rstrip('/')}/v3/conversations"
        body = {
            "bot": {"id": f"28:{self._app_id}", "name": "AR Copilot"},
            "isGroup": True,
            "topicName": topic,
            "members": [{"id": email} for email in member_emails],
            "channelData": {"tenant": {"id": self._tenant_id}},
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json=body,
            )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Bot Connector create conversation failed: {resp.status_code} {resp.text}")

        conversation_id = resp.json()["id"]
        await self._service_url_store.record_service_url(conversation_id, service_url)
        return conversation_id


_messenger: Optional[TeamsMessenger] = None


def get_messenger() -> TeamsMessenger:
    """FakeMessenger today; swaps to BotFrameworkMessenger automatically once
    MICROSOFT_APP_ID/MICROSOFT_APP_PASSWORD are both set — no call-site changes."""
    global _messenger
    if _messenger is None:
        from app.config import get_settings

        s = get_settings()
        _messenger = BotFrameworkMessenger() if (s.MICROSOFT_APP_ID and s.MICROSOFT_APP_PASSWORD) else FakeMessenger()
    return _messenger
