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

import re
from datetime import date
from typing import Optional

PRE_DUE_STAGE = "S0_pre_due"

# Deliberately simple -- good enough to catch typos before we hand the
# address to a real mail transport, not a full RFC 5322 validator.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PolicyViolation(Exception):
    """A write was refused by policy, before it ever reached the backend."""


def check_snooze_allowed(current_stage_code: Optional[str]) -> None:
    if current_stage_code == PRE_DUE_STAGE:
        raise PolicyViolation("The Pre-Due stage cannot be snoozed -- there's nothing to pause yet.")


def check_nonempty_text(value: Optional[str], field_name: str) -> None:
    if not value or not value.strip():
        raise PolicyViolation(f"{field_name} is required for this action.")


def check_valid_email(value: Optional[str]) -> None:
    if not value or not _EMAIL_RE.match(value.strip()):
        raise PolicyViolation("Enter a valid email address.")


def check_positive_int(value: int, field_name: str) -> None:
    if value < 1:
        raise PolicyViolation(f"{field_name} must be at least 1.")


def check_future_or_today(value: Optional[str], field_name: str) -> None:
    """value is an ISO date string (YYYY-MM-DD)."""
    if not value:
        return
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise PolicyViolation(f"{field_name} must be a valid date.")
    if parsed < date.today():
        raise PolicyViolation(f"{field_name} can't be in the past.")
