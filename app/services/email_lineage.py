"""
V2-compatible email lineage (added 2026-07-16, for the Teams-triggered
manual follow-up feature): builds the exact SMTP headers and hidden HTML
footer Lummus's own V2 dunning engine stamps on outbound email
(backend/app/dunning_v2/actions/internal_email_renderer.py), so a reply to
one of these follow-ups is recognized by Lummus's real inbound-reply
detector (backend/app/dunning_v2/inbound/detector.py) exactly the same way
a reply to an automated dunning email is -- no new inbound-capture
mechanism needed on this side, verified against that detector's source
rather than guessed.

Format is deliberately copied, not reinvented:
  - Headers: X-Dunning-Engine: v2, plus X-Dunning-Case-Id / -Invoice-No /
    -Stage where known (Graph allows at most 5 custom headers -- detector.py
    only looks for these four plus X-Dunning-Action-Id, which this feature
    has no equivalent of and omits).
  - Footer: a hidden `DUNNING-V2 | case_id: <id> | ...` div, survives
    quote-on-reply even if a mail client strips custom headers (detector.py
    falls back to this "belt + suspenders" signal).
"""
from __future__ import annotations

from typing import List, Optional, Tuple


def build_lineage_headers(
    case_id: str,
    invoice_no: Optional[str] = None,
    stage_code: Optional[str] = None,
) -> List[Tuple[str, str]]:
    headers: List[Tuple[str, str]] = [("X-Dunning-Engine", "v2"), ("X-Dunning-Case-Id", case_id)]
    if invoice_no:
        headers.append(("X-Dunning-Invoice-No", invoice_no))
    if stage_code:
        headers.append(("X-Dunning-Stage", stage_code))
    return headers


def build_lineage_footer(
    case_id: str,
    invoice_no: Optional[str] = None,
    project_number: Optional[str] = None,
    stage_code: Optional[str] = None,
) -> str:
    """A hidden div appended to the email body -- invisible to the reader,
    parsed by Lummus's detector._extract_from_footer if the reply is a
    forward/quote that stripped the custom headers."""
    tokens = [f"case_id: {case_id}"]
    if invoice_no:
        tokens.append(f"invoice_no: {invoice_no}")
    if project_number:
        tokens.append(f"project_number: {project_number}")
    if stage_code:
        tokens.append(f"stage_code: {stage_code}")
    marker = "DUNNING-V2 | " + " | ".join(tokens)
    return f'<div style="display:none">{marker}</div>'
