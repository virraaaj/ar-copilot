"""
POST /api/messages — the inbound Bot Framework HTTP edge bot.py's own
docstring flagged as not existing yet (added 2026-07-17). This is the
only place a real Bot Framework Activity gets parsed; everything below it
(TeamsBot, AgentLoop) stays exactly as tested against the hand-built
IncomingActivity shape.

Every request must carry a valid Bot Framework-issued bearer token (see
auth.py) -- this endpoint runs real write tools (snooze/comment/follow-up)
on behalf of whoever the token's claims say sent the message, so an
unauthenticated request must never reach TeamsBot.handle().
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.agent.loop import AgentLoop
from app.agent.setup import build_registry
from app.channels.teams.auth import TokenValidationError, validate_bot_framework_token
from app.channels.teams.bot import IncomingActivity, TeamsBot
from app.channels.teams.messenger import get_messenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.services.azure_openai import get_llm
from app.services.backend_client import BackendClient, get_backend_client
from app.services.chase_store import ChaseStore
from app.services.email_sender import get_email_sender

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_incoming_activity(activity: Dict[str, Any]) -> IncomingActivity:
    conversation_id = activity["conversation"]["id"]
    sender = activity.get("from") or {}
    user_id = sender.get("aadObjectId") or sender.get("id") or ""
    user_name = sender.get("name") or ""

    # Action.Submit card taps arrive as a "message" activity with `value`
    # set and `text` absent -- treat that as card_data, same distinction
    # IncomingActivity already made for the hand-built test fixtures.
    value = activity.get("value")
    if value is not None:
        return IncomingActivity(conversation_id=conversation_id, user_id=user_id, user_name=user_name, card_data=value)
    return IncomingActivity(conversation_id=conversation_id, user_id=user_id, user_name=user_name, text=activity.get("text"))


@router.post("/api/messages")
async def receive_activity(
    request: Request,
    authorization: str = Header(default=None),
    backend: BackendClient = Depends(get_backend_client),
) -> Dict[str, Any]:
    try:
        validate_bot_framework_token(authorization)
    except TokenValidationError as exc:
        logger.warning("Rejected /api/messages request: %s", exc)
        raise HTTPException(status_code=401, detail="Unauthorized")

    activity = await request.json()

    conversation_id = activity.get("conversation", {}).get("id")
    service_url = activity.get("serviceUrl")
    project_store = ProjectConversationStore()
    if conversation_id and service_url:
        await project_store.record_service_url(conversation_id, service_url)

    # Only "message" activities (plain text or an Action.Submit's value)
    # go through TeamsBot -- conversationUpdate (bot added/removed),
    # installationUpdate, etc. are acknowledged (serviceUrl already
    # captured above) but don't drive any agent behavior.
    if activity.get("type") == "message" and conversation_id:
        registry = build_registry()
        llm = get_llm()
        loop = AgentLoop(llm=llm, registry=registry, backend_client=backend)
        bot = TeamsBot(
            agent_loop=loop,
            messenger=get_messenger(),
            project_conversation_store=project_store,
            chase_store=ChaseStore(),
            backend_client=backend,
            email_sender=get_email_sender(),
            llm=llm,
        )
        await bot.handle(_to_incoming_activity(activity))

    return {}
