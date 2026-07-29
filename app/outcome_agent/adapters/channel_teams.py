"""Teams inbound helper — advance case with reply."""
from __future__ import annotations

from typing import Any, Dict, Optional


async def handle_teams_reply(
    case_row_id: str,
    text: str,
    settings,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    from app.outcome_agent.loop.scheduler import advance_case_with_reply

    return await advance_case_with_reply(case_row_id, text, settings, db_path=db_path)
