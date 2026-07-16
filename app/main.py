"""FastAPI entry point: serves the API (app/channels/web.py) and the built
Vite UI as static files (PLAN.md §5 Phase 2)."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.channels.web import router as web_router
from app.config import get_settings

logging.basicConfig(level=get_settings().LOG_LEVEL)

app = FastAPI(title="AR Copilot")
app.include_router(web_router)

UI_DIST = Path(__file__).parent.parent / "ui" / "dist"
if UI_DIST.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIST), html=True), name="ui")
else:
    logging.getLogger(__name__).warning(
        "ui/dist not found -- run `npm run build` in ui/ to serve the UI. API routes under /api still work."
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
