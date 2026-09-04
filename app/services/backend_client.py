"""
Async client for the Lummus AR/dunning backend.

Every method here maps to a real endpoint already serving the web frontend, so
this is just another authenticated API client. Auth uses the same OAuth2
password flow the frontend / UAT client use (POST /api/v1/auth/login), and the
token is cached + refreshed on 401.

This is the ONLY way this project talks to Lummus data — never connect to its
database directly (see PLAN.md §1).

Endpoint references (from the main repo):
  - Auth:           POST /api/v1/auth/login                (form-encoded)
  - Cases:          GET  /api/v2/dunning/cases
                    GET  /api/v2/dunning/cases/{id}
                    GET  /api/v2/dunning/cases/{id}/timeline
                    POST /api/v2/dunning/cases/{id}/pause|resume|close|handoff
                    POST /api/v2/dunning/response-events
                    GET  /api/v2/dunning/review-tasks
  - Contacts:       GET  /api/v1/dunning/project-contacts
                    GET  /api/v1/dunning/projects/{pn}/contacts
                    POST /api/v1/dunning/project-contacts
                    PATCH/DELETE /api/v1/dunning/project-contacts/{id}
  - Reference:      GET  /api/v1/dunning/business-units
  - Comms:          GET  /api/v1/communications/jobs
  - Aging:          GET  /api/v1/dunning/aging-table
                    POST /api/v1/dunning/aging-table/sync   (multipart)
  - Engine tick:    POST /api/v1/test/trigger-tick          (test-mode only)

Ported from lummus-teams-bot/services/backend_client.py — see PLAN.md §1.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Valid project-contact types accepted by the backend (mirrors
# src/constants/projectContactTypes.ts). "finance" is normalised to "bu_finance".
CONTACT_TYPES = ["pm", "bu_finance", "corp_finance", "general_manager", "legal"]


class BackendError(Exception):
    """Raised when a backend call fails; message is safe to surface to the user."""


class BackendClient:
    def __init__(self, base_url: str, service_email: str, service_password: str):
        self._base = base_url.rstrip("/")
        self._email = service_email
        self._password = service_password
        self._token: Optional[str] = None
        self._client = httpx.AsyncClient(base_url=self._base, timeout=60.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "BackendClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------ auth
    async def _login(self) -> None:
        form = {"username": self._email, "password": self._password}
        resp = await self._client.post(
            "/api/v1/auth/login",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            raise BackendError(f"Backend login failed ({resp.status_code}).")
        self._token = resp.json().get("access_token")
        if not self._token:
            raise BackendError("Backend login returned no access token.")

    async def verify_login(self) -> None:
        """Public wrapper around _login() — raises BackendError on bad
        credentials. Used by the web channel's login endpoint (Phase 2) to
        confirm a submitted email/password are real Lummus credentials,
        without exposing the private auth mechanics."""
        await self._login()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Any] = None,
        files: Optional[Any] = None,
        data: Optional[Any] = None,
        _retry: bool = True,
    ) -> httpx.Response:
        if self._token is None:
            await self._login()
        headers = {"Authorization": f"Bearer {self._token}"}
        resp = await self._client.request(
            method, path, params=params, json=json, files=files, data=data, headers=headers
        )
        if resp.status_code == 401 and _retry:
            # token expired — re-login once and retry
            self._token = None
            return await self._request(
                method, path, params=params, json=json, files=files, data=data, _retry=False
            )
        return resp

    @staticmethod
    def _ok_or_raise(resp: httpx.Response, what: str) -> Any:
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text[:200]
            raise BackendError(f"{what} failed ({resp.status_code}): {detail}")
        try:
            return resp.json()
        except Exception:
            return {}

    @staticmethod
    def _unwrap_list(payload: Any) -> List[Any]:
        if isinstance(payload, dict):
            return payload.get("items", payload.get("data", []))
        return payload if isinstance(payload, list) else []

    # ------------------------------------------------------------------ cases
    async def list_cases(self, **filters: Any) -> List[Dict[str, Any]]:
        """Filters: case_status, business_unit_id, customer_id, project_id,
        invoice_no, current_stage_code, limit."""
        params = {k: v for k, v in filters.items() if v not in (None, "", "all")}
        params.setdefault("limit", 50)
        resp = await self._request("GET", "/api/v2/dunning/cases", params=params)
        return self._unwrap_list(self._ok_or_raise(resp, "List cases"))

    async def get_case(self, case_id: str) -> Dict[str, Any]:
        resp = await self._request("GET", f"/api/v2/dunning/cases/{case_id}")
        return self._ok_or_raise(resp, "Get case")

    # ------------------------------------------------------- invoices (raw)
    # /api/v2/dunning/cases only ever returns invoices that already have a
    # dunning case -- a genuinely different, case-independent invoice
    # listing (added 2026-07-24, found live: an uploaded aging sheet had 2
    # invoices, one not yet overdue, and the not-yet-due one never showed
    # up anywhere in the app since its case-feeder hasn't created a case
    # for it yet and won't until it's actually overdue -- by design). This
    # hits the real Invoice table directly, not the dunning_cases table.
    async def list_invoices_raw(self, status: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        resp = await self._request("GET", "/api/v1/dunning/invoices", params=params)
        return self._unwrap_list(self._ok_or_raise(resp, "List invoices (raw)"))

    async def get_invoice_by_id(self, invoice_id: str) -> Dict[str, Any]:
        resp = await self._request("GET", f"/api/v1/dunning/invoices/{invoice_id}")
        return self._ok_or_raise(resp, "Get invoice (raw)")

    async def get_case_timeline(self, case_id: str, limit: int = 25) -> List[Dict[str, Any]]:
        resp = await self._request(
            "GET", f"/api/v2/dunning/cases/{case_id}/timeline", params={"limit": limit}
        )
        return self._unwrap_list(self._ok_or_raise(resp, "Get case timeline"))

    async def pause_case(self, case_id: str, reason: str, ends_at: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"reason": reason}
        if ends_at:
            # The web form's <input type="date"> (and the agent, which was
            # told "ISO date") only ever produce a bare YYYY-MM-DD, but the
            # real backend's Pydantic schema requires a full ISO datetime --
            # confirmed live: a bare date 422s with "invalid datetime
            # separator, expected `T`...". Normalize to midnight on that
            # date rather than push this backend quirk onto every caller.
            body["ends_at"] = ends_at if "T" in ends_at else f"{ends_at}T00:00:00"
        resp = await self._request("POST", f"/api/v2/dunning/cases/{case_id}/pause", json=body)
        return self._ok_or_raise(resp, "Snooze invoice")

    async def resume_case(self, case_id: str) -> Dict[str, Any]:
        resp = await self._request("POST", f"/api/v2/dunning/cases/{case_id}/resume", json={})
        return self._ok_or_raise(resp, "Resume invoice")

    async def close_case(self, case_id: str, reason: str, outcome: str = "closed_other") -> Dict[str, Any]:
        body = {"reason": reason, "outcome": outcome}
        resp = await self._request("POST", f"/api/v2/dunning/cases/{case_id}/close", json=body)
        return self._ok_or_raise(resp, "Close invoice")

    async def handoff_case(self, case_id: str, reason: str) -> Dict[str, Any]:
        resp = await self._request(
            "POST", f"/api/v2/dunning/cases/{case_id}/handoff", json={"reason": reason}
        )
        return self._ok_or_raise(resp, "Hand off invoice")

    async def log_response_event(
        self,
        case_id: str,
        raw_excerpt: str,
        source_channel: str = "manual_only",
        invoice_id: Optional[str] = None,
        suggested_category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """POSTs to /api/v2/dunning/response-events. Field names and the
        source_channel enum (email/voice_call/sms/manual_only/teams) are
        matched exactly against ResponseEventCreate (backend/app/dunning_v2/
        api/schemas.py) and StageChannel (backend/app/dunning_v2/enums.py) --
        verified against source, not assumed. An earlier version of this
        method sent response_category/raw_text (neither field exists on the
        real schema) and defaulted source_channel to the invalid value
        "ar_copilot" -- FastAPI/Pydantic silently drops unrecognized fields
        rather than erroring, so every comment logged through it had its
        text silently discarded, and the invalid default channel would have
        hard-failed with a 422 the moment anything relied on it doing so.
        `record_response()` (backend/app/dunning_v2/reviews/service.py) always
        emits a REPLY_RECEIVED timeline event, so a correctly-logged comment
        here does show up via get_case_timeline()."""
        body: Dict[str, Any] = {
            "case_id": case_id,
            "source_channel": source_channel,
            "raw_excerpt": raw_excerpt,
        }
        if invoice_id:
            body["invoice_id"] = invoice_id
        if suggested_category:
            body["suggested_category"] = suggested_category
        resp = await self._request("POST", "/api/v2/dunning/response-events", json=body)
        return self._ok_or_raise(resp, "Log response")

    async def list_review_tasks(self, case_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {"case_id": case_id} if case_id else None
        resp = await self._request("GET", "/api/v2/dunning/review-tasks", params=params)
        return self._unwrap_list(self._ok_or_raise(resp, "List review tasks"))

    # ------------------------------------------------------------- contacts
    async def list_all_project_contacts(self) -> List[Dict[str, Any]]:
        resp = await self._request("GET", "/api/v1/dunning/project-contacts")
        return self._unwrap_list(self._ok_or_raise(resp, "List project contacts"))

    async def list_contacts_for_project(self, project_number: str) -> List[Dict[str, Any]]:
        # This endpoint's real response shape is {"project_number": ...,
        # "contacts": [...]} -- distinct from list_all_project_contacts'
        # {"items": [...]} shape, so _unwrap_list (which only recognizes
        # "items"/"data") silently returned [] here always. Found live
        # 2026-07-20: the chase engine's PM-email resolution, the
        # get_project_contacts chat tool, and proactive.py's reminder
        # contact lookup were all silently getting no contacts back from
        # this call since it was added -- every test had mocked the wrong
        # ("items") shape, which is why this went unnoticed until a live run.
        resp = await self._request(
            "GET", f"/api/v1/dunning/projects/{project_number}/contacts"
        )
        payload = self._ok_or_raise(resp, "List project contacts")
        return payload.get("contacts", []) if isinstance(payload, dict) else []

    async def add_project_contact(
        self,
        project_number: str,
        contact_type: str,
        name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        contact_type = "bu_finance" if contact_type == "finance" else contact_type
        if contact_type not in CONTACT_TYPES:
            raise BackendError(f"contact_type must be one of: {', '.join(CONTACT_TYPES)}")
        body = {
            "project_number": project_number,
            "contact_type": contact_type,
            "name": name,
            "email": email,
            "phone": phone,
        }
        resp = await self._request("POST", "/api/v1/dunning/project-contacts", json=body)
        return self._ok_or_raise(resp, "Add project contact")

    async def update_project_contact(self, contact_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self._request(
            "PATCH", f"/api/v1/dunning/project-contacts/{contact_id}", json=fields
        )
        return self._ok_or_raise(resp, "Update project contact")

    async def delete_project_contact(self, contact_id: str) -> Dict[str, Any]:
        resp = await self._request("DELETE", f"/api/v1/dunning/project-contacts/{contact_id}")
        return self._ok_or_raise(resp, "Delete project contact")

    # ------------------------------------------------- default contacts
    # Mirrors Lummus's own "Default Project Contacts" page design/concept
    # (backend/app/api/v1/dunning.py) 1:1 -- a template system, not a live
    # link: one Global scope (bu=None) plus optional per-BU override
    # scopes, one contact per contact_type per scope. Real Lummus applies
    # these to seed new projects' contacts at creation time; this app
    # exposes the same CRUD surface for the same "set it once, applies
    # everywhere unless overridden" mental model, verified against the
    # real endpoint/response shapes in that file, not guessed.
    async def list_default_project_contacts(self) -> List[Dict[str, Any]]:
        """Returns scopes: [{bu, bu_name, contacts: [{contact_id,
        contact_type, name, email, phone}, ...]}, ...] -- bu=None is
        always present first (the Global scope)."""
        resp = await self._request("GET", "/api/v1/dunning/default-project-contacts")
        return self._ok_or_raise(resp, "List default project contacts")

    async def add_default_project_contact(
        self,
        contact_type: str,
        bu: Optional[str] = None,
        name: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Dict[str, Any]:
        contact_type = "bu_finance" if contact_type == "finance" else contact_type
        if contact_type not in CONTACT_TYPES:
            raise BackendError(f"contact_type must be one of: {', '.join(CONTACT_TYPES)}")
        body = {"contact_type": contact_type, "bu": bu, "name": name, "email": email, "phone": phone}
        resp = await self._request("POST", "/api/v1/dunning/default-project-contacts", json=body)
        return self._ok_or_raise(resp, "Add default project contact")

    async def update_default_project_contact(self, contact_id: str, fields: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self._request("PATCH", f"/api/v1/dunning/default-project-contacts/{contact_id}", json=fields)
        return self._ok_or_raise(resp, "Update default project contact")

    async def delete_default_project_contact(self, contact_id: str) -> Dict[str, Any]:
        resp = await self._request("DELETE", f"/api/v1/dunning/default-project-contacts/{contact_id}")
        return self._ok_or_raise(resp, "Delete default project contact")

    # ------------------------------------------------------------ reference
    async def list_business_units(self) -> List[Dict[str, Any]]:
        resp = await self._request("GET", "/api/v1/dunning/business-units")
        return self._unwrap_list(self._ok_or_raise(resp, "List business units"))

    async def list_comms_jobs(self, case_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if case_id:
            params["case_id"] = case_id
        resp = await self._request("GET", "/api/v1/communications/jobs", params=params)
        return self._unwrap_list(self._ok_or_raise(resp, "List communication jobs"))

    # --------------------------------------------------------------- engine
    async def trigger_tick(self, reference_date: Optional[str] = None) -> Dict[str, Any]:
        """Run the engine now. Requires the backend to allow the test endpoint
        (DUNNING_TEST_MODE). In production the scheduler ticks automatically."""
        body = {"reference_date": reference_date} if reference_date else {}
        resp = await self._request("POST", "/api/v1/test/trigger-tick", json=body)
        return self._ok_or_raise(resp, "Trigger engine tick")

    async def list_aging_table(self, limit: int = 5000, bu_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Latest aging snapshot row per invoice — the only place the real AR
        open balance lives.

        `/api/v1/dunning/invoices` deliberately does NOT carry it: Lummus keeps
        `invoices.amount` as the invoice *face* amount and refuses to let the
        open balance overwrite it (see `_invoice_amount_from_row` in the Lummus
        repo), and `InvoiceResponse` has no open_amount field at all. Reading
        the balance from `amount` therefore overstates every partially-paid
        invoice — a $3.27M invoice with $318k still outstanding reads as
        $3.27M. Rows here are keyed by `invoice_number`.

        Note this endpoint returns the *latest* row per invoice, which survives
        payment — a fully paid invoice keeps its last snapshot row with a
        stale open_amount. Paid must therefore be read from the invoice's
        status, not inferred from this feed. Added 2026-09-04.

        Paginated (2026-09-04): the endpoint caps its own `limit` query param
        at 5000 (`le=5000` server-side) and returns 200 OK either way, so a
        book larger than one page used to truncate silently -- no exception,
        no signal -- and the missing invoices fell through to the per-invoice
        face-amount fallback with nothing to flag it. Loops on `skip` until a
        short page comes back, capped so a misbehaving backend (e.g. always
        returning a full page) can't spin forever.
        """
        page_size = min(limit, 5000)
        params: Dict[str, Any] = {}
        if bu_id:
            params["bu_id"] = bu_id
        rows: List[Dict[str, Any]] = []
        skip = 0
        max_pages = 200  # 200 * 5000 = 1,000,000 rows -- far beyond any real book
        for _ in range(max_pages):
            page_params = dict(params, skip=skip, limit=page_size)
            resp = await self._request("GET", "/api/v1/dunning/aging-table", params=page_params)
            page = self._unwrap_list(self._ok_or_raise(resp, "List aging table"))
            rows.extend(page)
            if len(page) < page_size:
                break
            skip += page_size
        return rows

    async def sync_aging_excel(self, filename: str, content: bytes) -> Dict[str, Any]:
        files = {
            "file": (
                filename,
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        }
        resp = await self._request("POST", "/api/v1/dunning/aging-table/sync", files=files)
        return self._ok_or_raise(resp, "Sync aging Excel")


_client: Optional[BackendClient] = None


def get_backend_client() -> BackendClient:
    """Process-wide singleton, built from Settings. FastAPI dependency-injects
    this; the CLI/smoke script can also call it directly."""
    global _client
    if _client is None:
        from app.config import get_settings

        s = get_settings()
        _client = BackendClient(s.BACKEND_API_URL, s.BACKEND_SERVICE_EMAIL, s.BACKEND_SERVICE_PASSWORD)
    return _client
