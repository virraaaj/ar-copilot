"""FastAPI entry point: serves the API (app/channels/web.py) and the built
Vite UI as static files (PLAN.md §5 Phase 2).

Route registration order matters here and is easy to get wrong: API routes
and /health must be registered before anything that could swallow their
paths. The UI is a client-side-routed SPA (React Router), so a hard refresh
on e.g. /chat must still serve index.html and let the client take over --
mounting StaticFiles at "/" does NOT do this by default (it 404s any path
that isn't a real file), so the SPA fallback below is a real fix, not
decoration.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable, List

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.channels.teams.http import router as teams_http_router
from app.channels.teams.messenger import get_messenger
from app.channels.teams.project_conversation_store import ProjectConversationStore
from app.channels.teams.proactive import ReminderDedup, send_due_reminders
from app.channels.web import router as web_router
from app.config import get_settings
from app.services.backend_client import BackendError, get_backend_client
from app.services.azure_openai import get_llm
from app.services.chase_engine import poll_chase_mailbox, run_chase_tick
from app.services.chase_store import ChaseStore
from app.services.digest_engine import send_project_digests
from app.services.digest_store import DigestStore
from app.services.email_sender import get_email_sender
from app.services.followup_engine import mirror_new_replies_to_teams, send_due_followups
from app.services.followup_store import FollowUpStore
from app.services.graph_mailbox import get_mailbox_reader

logging.basicConfig(level=get_settings().LOG_LEVEL)
logger = logging.getLogger(__name__)


async def _poll_loop(name: str, interval_seconds: int, fn: Callable[[], Awaitable[int]]) -> None:
    """Runs `fn` every `interval_seconds` forever, until the task is
    cancelled at shutdown. A single tick's failure (e.g. the Lummus
    backend is briefly unreachable) logs and waits for the next tick
    rather than killing the loop -- these are background jobs nobody is
    watching in real time, so silently stopping would be worse than a
    logged failure."""
    while True:
        try:
            count = await fn()
            if count:
                logger.info("%s: %d sent", name, count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("%s poll tick failed", name)
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Actually runs the background pollers this app has had sitting
    around unused since Phase 4/the follow-up feature: send_due_reminders,
    send_due_followups + mirror_new_replies_to_teams, and
    send_project_digests all previously only ran when someone (me, this
    session) manually invoked them from a script. Each is gated by its
    own *_POLL_ENABLED flag (all default False) so nothing fires
    unexpectedly in a fresh checkout."""
    s = get_settings()
    tasks: List[asyncio.Task] = []

    backend = get_backend_client()
    messenger = get_messenger()
    project_store = ProjectConversationStore()
    # Always constructed (cheap, lazy schema) so send_due_followups can skip
    # cases the chase engine already owns even when CHASE_ENABLED is False --
    # harmless no-op in that case, since no chase rows exist to skip.
    chase_store = ChaseStore()

    if s.PROACTIVE_POLL_ENABLED:
        dedup = ReminderDedup()

        async def run_reminders() -> int:
            return await send_due_reminders(backend, messenger, project_store, dedup)

        tasks.append(asyncio.create_task(_poll_loop("reminders", s.PROACTIVE_POLL_INTERVAL_SECONDS, run_reminders)))

        followup_store = FollowUpStore()
        email_sender = get_email_sender()

        async def run_followups() -> int:
            sent = await send_due_followups(backend, email_sender, followup_store, chase_store)
            mirrored = await mirror_new_replies_to_teams(backend, followup_store, project_store, messenger)
            return sent + mirrored

        tasks.append(asyncio.create_task(_poll_loop("followups", s.FOLLOWUP_POLL_INTERVAL_SECONDS, run_followups)))

    if s.DIGEST_POLL_ENABLED:
        digest_store = DigestStore()

        async def run_digests() -> int:
            return await send_project_digests(backend, messenger, project_store, digest_store)

        tasks.append(asyncio.create_task(_poll_loop("digests", s.DIGEST_POLL_INTERVAL_SECONDS, run_digests)))

    if s.CHASE_ENABLED:
        email_sender_for_chases = get_email_sender()
        chase_llm = get_llm()  # powers the composer/smart-escalation AI features when their flags are on

        async def run_chase() -> int:
            return await run_chase_tick(backend, messenger, email_sender_for_chases, chase_store, project_store, s, chase_llm)

        tasks.append(asyncio.create_task(_poll_loop("chase", s.CHASE_POLL_INTERVAL_SECONDS, run_chase)))

    if s.CHASE_MAIL_POLL_ENABLED:
        llm = get_llm()

        async def run_chase_mail() -> int:
            return await poll_chase_mailbox(
                get_mailbox_reader(), llm, backend, messenger, get_email_sender(), chase_store, project_store, s
            )

        tasks.append(asyncio.create_task(_poll_loop("chase_mail", s.CHASE_MAIL_POLL_INTERVAL_SECONDS, run_chase_mail)))

    yield

    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="AR Copilot", lifespan=lifespan)

# API + health first -- must win over the SPA catch-all registered below.
app.include_router(web_router)
app.include_router(teams_http_router)


@app.exception_handler(httpx.RequestError)
async def backend_unreachable_handler(request: Request, exc: httpx.RequestError) -> JSONResponse:
    """Every endpoint that talks to the Lummus backend (or Graph, for
    SharePoint) can hit this if that service is simply unreachable -- a
    connection failure, not a bad response, so BackendClient's own
    status-code handling never sees it. Without this, it surfaces as a raw
    500 with a stack trace; with it, every route gets a clean 503 for free
    instead of needing its own try/except."""
    logger.warning("Upstream request failed: %s", exc)
    return JSONResponse(status_code=503, content={"detail": f"An upstream service is unreachable: {exc}"})


@app.exception_handler(BackendError)
async def backend_rejected_handler(request: Request, exc: BackendError) -> JSONResponse:
    """The Lummus backend responded, but rejected the request (a business
    rule, not a connection problem) -- e.g. pausing an already-closed case.
    BackendError's message is written to be safe to surface (see its
    docstring), but nothing previously caught it outside a route's own
    try/except, so any uncaught case (like this one, found by actually
    exercising the new snooze endpoint against live UAT data rather than
    only mocked-success tests) leaked as a raw 500. 422 since these are
    almost always "the request was rejected", not a server fault."""
    logger.info("Backend rejected request: %s", exc)
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


UI_DIST = Path(__file__).parent.parent / "ui" / "dist"
if UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(UI_DIST / "assets")), name="ui-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        """Anything not already matched above (API routes, /health,
        /assets/*) falls through to here. Serves the exact file if it exists
        (favicon.svg etc. from Vite's public/ dir), otherwise index.html so
        React Router can handle client-side routes like /chat on a direct
        load or hard refresh."""
        candidate = UI_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(UI_DIST / "index.html")
else:
    logger.warning("ui/dist not found -- run `npm run build` in ui/ to serve the UI. API routes under /api still work.")
