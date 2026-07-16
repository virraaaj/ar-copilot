"""
TeamsMessenger — swappable interface for sending into Teams (PLAN.md §5
Phase 4). Blocked on a real Bot Framework credential: checked (not assumed)
that MICROSOFT_APP_ID/MICROSOFT_APP_PASSWORD are both unset everywhere
accessible, same situation as Azure OpenAI.

FakeMessenger records everything sent — what bot.py and proactive.py's tests
assert against. A real Bot-Framework-backed implementation drops in later
with no call-site changes, same pattern already used for the LLM client
(azure_openai.py), the search index (documents/index.py), and the
SharePoint source (documents/sources/sharepoint.py).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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


class BotFrameworkMessenger(TeamsMessenger):
    """Real implementation via the Bot Framework REST API / SDK. Blocked —
    no MICROSOFT_APP_ID/MICROSOFT_APP_PASSWORD provisioned yet."""

    def __init__(self) -> None:
        from app.config import get_settings

        s = get_settings()
        if not s.MICROSOFT_APP_ID or not s.MICROSOFT_APP_PASSWORD:
            raise RuntimeError(
                "BotFrameworkMessenger requires MICROSOFT_APP_ID and MICROSOFT_APP_PASSWORD. "
                "Use FakeMessenger (the default via get_messenger()) until both are set."
            )
        raise NotImplementedError("BotFrameworkMessenger: implement once Bot Framework credentials are provisioned.")

    # ABCMeta checks abstractness (and refuses to instantiate) BEFORE __init__
    # runs at all -- these stubs exist purely so the class is concrete enough
    # to reach the __init__ guard above. They're unreachable: __init__ always
    # raises first, on either the missing-credential path or the
    # not-yet-implemented path.
    async def send_text(self, conversation_id: str, text: str) -> None:
        raise NotImplementedError

    async def send_card(self, conversation_id: str, card: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def create_group_conversation(self, topic: str, member_emails: List[str]) -> str:
        raise NotImplementedError


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
