"""
Write-action policy rules (PLAN.md §5 Phase 3). Checked before a write tool
calls the backend at all, so a refusal is a clean, explained error the model
(or a Teams card handler) can relay directly — not a raw backend 400 to
interpret.

A PolicyViolation raised from inside a tool handler is caught generically by
AgentLoop (loop.py) and turned into `{"error": str(exc)}` fed back to the
model — no per-tool try/except needed there. Teams card actions (Phase 4),
which call these tools directly rather than through the loop, catch it
themselves to render a clean error card.
"""
from __future__ import annotations

from typing import Optional

PRE_DUE_STAGE = "S0_pre_due"


class PolicyViolation(Exception):
    """A write was refused by policy, before it ever reached the backend."""


def check_snooze_allowed(current_stage_code: Optional[str]) -> None:
    if current_stage_code == PRE_DUE_STAGE:
        raise PolicyViolation("The Pre-Due stage cannot be snoozed -- there's nothing to pause yet.")


def check_nonempty_text(value: Optional[str], field_name: str) -> None:
    if not value or not value.strip():
        raise PolicyViolation(f"{field_name} is required for this action.")
