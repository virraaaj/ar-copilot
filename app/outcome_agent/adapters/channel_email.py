"""Email channel helpers for outcome agent."""
from __future__ import annotations

import re
from typing import Optional


TOKEN_RE = re.compile(r"\[?(AR-[A-F0-9]{6})\]?", re.IGNORECASE)


def extract_subject_token(subject: str) -> Optional[str]:
    if not subject:
        return None
    m = TOKEN_RE.search(subject)
    return m.group(1).upper() if m else None
