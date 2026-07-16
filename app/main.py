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

import logging
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.channels.web import router as web_router
from app.config import get_settings
from app.services.backend_client import BackendError

logging.basicConfig(level=get_settings().LOG_LEVEL)
logger = logging.getLogger(__name__)

app = FastAPI(title="AR Copilot")

# API + health first -- must win over the SPA catch-all registered below.
app.include_router(web_router)


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
