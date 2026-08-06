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


# Real email replies come back with the entire prior thread quoted below
# the new text (mail clients' default "reply" behavior) -- e.g. Gmail's
# "On <date> <sender> wrote:" marker, Outlook's "From: ... Sent: ..."
# header block, or a line of dashes before "Original Message". Added
# 2026-08-06 (found live, first real Graph-read reply): without this the
# reply interpreter sees both the customer's actual new text AND a full
# copy of the agent's own outbound email in the same string, which is
# extra noise at best and a source of confusion at worst.
_QUOTE_MARKERS_RE = re.compile(
    r"(\r?\n)+On .{0,80} wrote:\s*$|"
    r"(\r?\n)+-+\s*Original Message\s*-+|"
    r"(\r?\n)+From:\s.+(\r?\n)+Sent:\s|"
    r"(\r?\n)+>.*",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def strip_quoted_reply(body: str) -> str:
    """Best-effort: cut the body at the first quoted-thread marker,
    keeping only the customer's own new text above it. Falls back to the
    full body unchanged if no marker is found -- never returns empty
    just because a marker matched at position 0 on a malformed message."""
    if not body:
        return body
    m = _QUOTE_MARKERS_RE.search(body)
    if not m or m.start() == 0:
        return body.strip()
    return body[: m.start()].strip()
