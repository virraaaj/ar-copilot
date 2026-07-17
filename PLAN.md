# AR Copilot v2 — Build Plan (handoff)

A fresh-rebuild side project: a smarter, more complete Teams agent for the Lummus
AR/dunning domain, plus a minimal web UI for checking AR state. Personal
exploration for now — but stay close to Azure services so it could be pitched
later.

**Executor notes:** work phase by phase, in order. Do not start a phase until the
previous phase's acceptance criteria pass. Ask the user before any decision
marked ⚠️ OPEN. Create the project as a new folder `ar-copilot/` (git init, own
repo — do NOT put this inside the `Lummus` or `lummus-teams-bot` repos).

---

## 1. Context — what exists and what to reuse

Two prior codebases exist. **Read them for patterns; do not extend them.**

- `C:\Users\devco\OneDrive\Desktop\lummus-teams-bot` — a working MVP Teams bot
  (~2k lines, Python, Bot Framework SDK + Azure OpenAI). Worth stealing:
  - `services/backend_client.py` — a clean async httpx client for the main
    backend, including the login/token-refresh dance. Reuse this almost as-is.
  - `bot/guardrails.py` — admin allowlist + prompt-injection defenses. Good
    starting ideas, but v2's guardrails should be richer (see §6).
  - `bot/tools.py` — the existing tool registry (432 lines). Read it to see the
    tool-shape that worked, then redesign (it conflates read and write tools).
- `C:\Users\devco\OneDrive\Desktop\Lummus` — the main product. The backend API
  is the **only** data access path. Never connect to its DB directly.

### Backend API surface (verified against `backend_client.py`)

| Area | Endpoints |
|---|---|
| Auth | `POST /api/v1/auth/login` (form-encoded; JWT) |
| Cases | `GET /api/v2/dunning/cases`, `GET /cases/{id}`, `GET /cases/{id}/timeline`, `POST /cases/{id}/pause\|resume\|close\|handoff`, `POST /response-events`, `GET /review-tasks` |
| Contacts | `GET/POST /api/v1/dunning/project-contacts`, `PATCH/DELETE /project-contacts/{id}`, `GET /projects/{pn}/contacts` |
| Reference | `GET /api/v1/dunning/business-units` |
| Aging | `POST /api/v1/dunning/aging-table/sync` (multipart Excel) |
| Comms | `GET /api/v1/communications/jobs` |
| Engine tick | `POST /api/v1/test/trigger-tick` (test-mode only) |

### Dev environment

Point at the local Docker UAT stack (`docker-compose.test.yml` in the Lummus
repo): backend `http://localhost:8001`, login `uat-test@lummus.internal` /
`UATPassword123!`, Mailpit on `:8025`. It runs with `DUNNING_TEST_MODE=true`, so
`trigger-tick` and the reset endpoints exist. If containers aren't running:
`docker compose -f docker-compose.test.yml up -d` from the Lummus repo root.

### Domain facts the agent must respect (verified in code this session)

- Stage ladder: Pre-Due → Reminder → First Notice → Second Notice → Escalation
  → Final Notice → Collections Handoff (terminal).
- Voice calls are **PM-only by policy** — never the customer. Hardcoded in the
  backend dispatcher SQL.
- Terminology: "invoice" not "case" in user-facing text; the bot is the
  "AR agent", never "the bot".
- Pre-Due stage cannot be snoozed.

---

## 2. Product shape

Two clients over one shared agent core:

```
┌───────────┐   ┌─────────────┐
│ Teams bot  │   │ Minimal web │
│ (adapter)  │   │ UI (chat +  │
│            │   │  dashboard) │
└─────┬──────┘   └──────┬──────┘
      │                 │
      ▼                 ▼
┌─────────────────────────────┐
│        Agent core            │
│  loop · tool registry ·      │
│  guardrails · audit log      │
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│   Backend client (httpx)     │──▶ Lummus backend API
└─────────────────────────────┘
```

The agent core must be channel-agnostic: same loop, tools, and guardrails
whether the message came from Teams, the web UI, or a CLI test harness.

## 3. Tech stack (decided)

- **Python 3.12 + FastAPI** — one process serves the agent API, the web UI's
  static build, and (later) the Bot Framework messaging endpoint.
- **Azure OpenAI** tool-calling (`gpt-5-mini` deployment exists; reuse the env
  var names from `lummus-teams-bot/config.py`).
- **React + Vite + Tailwind** for the minimal UI (matches the main repo's stack).
- **SQLite** for the agent's own state (audit log, conversation refs, proactive
  dedupe, document metadata + auto-logged fields). Not Postgres — this project
  owns no business data.
- **pytest + respx** for tests (mock the backend API; no live calls in CI).

### Document Intelligence additions (Phase 1B — see §5)

- **Azure AI Document Intelligence** (formerly Form Recognizer) — OCR for
  scanned PDFs, layout extraction, and prebuilt/custom models for structured
  field extraction (Vendor Certifications' "auto-log important information").
  Azure-native, fits the existing constraint of no non-Azure AI services.
- **Azure AI Search** — hybrid text + vector index. One service covers text
  search (manuals, T&Cs, quote descriptions) and, later, image vector fields
  (quote drawing similarity) — avoids standing up a separate vector DB.
- **pypdf / pdfplumber** — native (non-scanned) PDF text extraction; cheaper
  and faster than OCR when a PDF already has a text layer. Try this first,
  fall back to Document Intelligence OCR only when a page has no extractable
  text (i.e. it's a scan/image).
- **Azure AI Vision multimodal embeddings** — for drawing/CAD image similarity
  search (Quotes, later phase). Deliberately deferred; text search ships first.

## 4. Repo layout

```
ar-copilot/
  PLAN.md                    ← this file, moved in
  app/
    main.py                  ← FastAPI entry
    config.py
    agent/
      loop.py                ← tool-calling loop
      registry.py            ← tool registration + read/write classification
      tools_read.py
      tools_write.py
      prompts.py
    guardrails/
      identity.py            ← who is this user, what role
      policy.py              ← what may this role do
      injection.py           ← retrieved-content hygiene
      audit.py               ← append-only audit log (SQLite)
    channels/
      web.py                 ← POST /api/chat (SSE streaming)
      teams.py               ← Bot Framework adapter (Phase 4)
    services/
      backend_client.py      ← adapted from lummus-teams-bot
      state.py               ← SQLite store
    proactive/
      watchers.py            ← Phase 5
    documents/                ← Phase 1B
      sources/
        base.py               ← DocumentSource interface (pluggable storage)
        sharepoint.py          ← Graph API connector
        manual_upload.py       ← direct upload path
      ingest.py                ← text-layer extraction, OCR fallback, chunking
      extract.py                ← structured field auto-logging (Vendor Certs)
      index.py                  ← Azure AI Search read/write wrapper
      tools_documents.py        ← agent tools: search_documents, get_document, …
  ui/                        ← Vite app
    src/pages/Dashboard.tsx
    src/pages/InvoiceDetail.tsx
    src/pages/Chat.tsx
    src/pages/Documents.tsx  ← Phase 1B: upload + browse + search
  tests/
```

---

## 5. Phases

### Phase 0 — Scaffold + backend client (½ day)
- New repo, layout above, config via env vars with a `.env.example`.
- Port `backend_client.py`; add typed wrappers for every endpoint in §1.
- **Accept:** `pytest` green; a smoke script logs into the local UAT backend and
  lists cases.

### Phase 1 — Agent core with read-only tools (the heart — do this well)
- Tool-calling loop against Azure OpenAI: system prompt → tool calls → final
  answer. Max 8 tool-call rounds, then answer with what it has.
- Read-only tools (all no-side-effect): `list_invoices` (filters: status,
  stage, BU, project, overdue-days), `get_invoice`, `get_timeline`,
  `get_project_contacts`, `list_review_tasks`, `aging_summary` (computed
  client-side from cases: totals by bucket/BU/project).
- The registry marks every tool `read` or `write`. Phase 1 registers zero write
  tools; the loop must *reject* (not silently skip) any write-tool call.
- **Invoice-ID-free resolution:** never ask the user for an invoice number.
  Search by whatever criteria they gave (customer name, project, aging bucket,
  amount range) via `list_invoices`. If the search returns more than one
  match, present a short disambiguation list using only human-readable fields
  (customer, amount, due date, stage) — never surface the raw invoice ID as
  something the user is expected to read back or type.
- CLI harness (`python -m app.cli "which invoices are worst?"`) for fast
  iteration without Teams or the UI.
- **Accept:** it answers multi-step questions that require chaining tools, e.g.
  "which project has the most money stuck past 60 days, and has its PM ever
  replied?" — verified against the UAT stack's seeded data. It refuses
  action requests with a clear "I can look things up but can't act yet."

### Phase 1B — Document Intelligence: foundation (boss must-have; runs after Phase 1, alongside Phase 2)

⚠️ New capability added 2026-07-15: the agent must also answer questions over
five document types — Vendor Certifications, Customer Manuals, Quotes, T&Cs,
Equipment Manuals. Building the shared ingestion/search foundation on the
**simplest** type first (Equipment Manuals) means Vendor Certs (adds
structured auto-extraction) and Quotes (adds drawing similarity) reuse it
rather than each building their own pipeline. This phase depends on Phase 1's
tool-calling loop existing — document search is just another tool the agent
decides to call.

- **Sources (pluggable from day one):** `DocumentSource` interface with two
  implementations — SharePoint (Graph API, same auth pattern as the main
  Lummus backend's document sync) and manual upload (through the web UI).
  Designed so a third source (network drive, other DMS) is a new class, not a
  rewrite.
- **Ingestion:** try native PDF text extraction first (pypdf/pdfplumber); if a
  page has no text layer (a scan), fall back to Azure AI Document Intelligence
  OCR. Chunk extracted text, embed with Azure OpenAI, write to Azure AI Search.
- **Tools (read-only, same registry as Phase 1):** `search_documents(query,
  doc_type)`, `get_document(doc_id)`, `list_recent_documents(doc_type)`.
- **UI:** a 4th page, `Documents.tsx` — upload, browse by type, and a search
  box that shows matched chunks with source document + page.
- **Guardrails:** document text is untrusted content, same as backend data —
  route it through the same injection-hygiene wrapping as §6.3 before it
  reaches the model. A malicious PDF is now a real threat surface, not just a
  hypothetical.
- **Accept:** upload an Equipment Manual PDF (one native, one scanned) through
  the UI; ask the agent a question whose answer only exists in that manual;
  it answers correctly and cites the source document. Same works for a
  document pulled from SharePoint instead of uploaded.

### Phase 1C — Document Intelligence: Vendor Certifications (auto-log)
- Builds on 1B's ingestion pipeline. Adds structured field extraction — define
  the field schema with the boss/business first (⚠️ OPEN, see §7) before
  building the extractor, since "important information" is undefined.
- Extracted fields land in a new SQLite table (`vendor_cert_fields`), queryable
  by a new tool (`get_vendor_cert_fields`), not just full-text search.
- **Accept:** upload a vendor certification PDF; the defined fields are
  extracted and correctly queryable without the agent needing to re-read the
  full document.

### Phase 1D — Document Intelligence: Quotes (text + drawing search)
- Text search over quote descriptions/part numbers reuses 1B's pipeline
  directly — no new work beyond indexing quotes as a doc_type.
- Drawing similarity is new: Azure AI Vision multimodal embeddings on
  extracted drawing images, stored as a vector field in the same Azure AI
  Search index. A drawing query embeds the same way and searches that field.
- **Accept:** given a reference part drawing, the agent returns quotes with
  visually similar drawings, ranked by similarity — verified against a small
  hand-picked set of known-similar and known-dissimilar drawings.

### Phase 1E — Document Intelligence: T&Cs (non-standard clause detection)
- Needs a "standard" T&C reference to diff against (⚠️ OPEN — see §7:
  whose T&Cs, is there a canonical version).
- Search reuses 1B; "non-standard" detection is a targeted comparison prompt
  (retrieve the standard clause for each section, compare against the
  uploaded document's corresponding clause, flag deltas) rather than a new
  pipeline.
- **Accept:** given a T&C document with 2-3 deliberately altered clauses, the
  agent correctly flags those clauses and not the unmodified ones.

### Phase 2 — Minimal web UI
Three pages, deliberately small (a 4th, Documents, is added in Phase 1B):
- **Dashboard** — aging summary tiles (total open, by bucket), invoice table
  (status/stage filters), snoozed list. Read-only. Each row gets an "Ask about
  this" action; clicking it opens the Chat page with that invoice pinned as
  context — shown to the user as a small chip (e.g. "Re: Meridian Bay — $1.25M,
  21 days overdue") above the input, not the raw ID. The user then types their
  own question in plain language; the ID rides along invisibly in the actual
  API call to the agent.
- **Invoice detail** — timeline, contacts, comments for one invoice.
- **Chat** — the same agent, streamed (SSE). This is the primary iteration
  surface for agent quality from here on.
- Login: reuse the backend's JWT login; store token in memory (this is a dev
  tool, not production auth).
- **Accept:** `npm run build` output served by FastAPI; all three pages work
  against the UAT stack. The Dashboard → Chat handoff correctly pins the
  invoice without the user ever seeing/typing its raw ID.

### Phase 3 — Write actions with confirm-before-execute
- Write tools: `snooze_invoice`, `resume_invoice`, `add_comment`. (`close_invoice`,
  `update_project_contact`, `trigger_outreach` deferred — not needed for the
  Teams reach-out work below; add if/when something actually calls for them.)
- Two-step protocol: agent proposes → returns a structured confirmation
  (action, target, params, consequence) → user confirms in UI/chat → only then
  execute. Never execute a write in the same turn it was proposed. On a card
  (Phase 4), the card *is* the proposal and submitting its form *is* the
  confirmation — no extra double-confirm turn on top of that.
- Every executed write goes to the audit log (who, what, when, tool args,
  backend response).
- Rules engine in `policy.py`: snooze on Pre-Due → refuse with the reason;
  every write requires a reason string; new `pm` role (§6) gets exactly these
  three write tools, nothing else.
- **Accept:** "snooze INV-X for 2 weeks, dispute" round-trips with confirmation;
  audit row exists; refusals tested for each policy rule.

### Phase 4 — Teams: proactive reach-out + conversational AI in the same chat

⚠️ Reworked again 2026-07-16 (same day, later): snooze/comment no longer
happen via in-Teams Adaptive Card forms. A reminder card's Snooze/Add
comment buttons are now `Action.OpenUrl` links carrying a signed,
short-lived magic-link token (`app/guardrails/magic_link.py`) that lands
the user already-authenticated on that exact invoice's web form. A plain-
text message expressing the same intent ("I want to snooze this") gets a
similar link to a picker page scoped to just that chat's project
invoices (`ProjectInvoicePicker.tsx`), since a project chat pools multiple
invoices and free text alone doesn't say which one. Reason: doing the
actual data entry on the web (where the comment/timeline UI already lives)
means one form to build and maintain instead of two, and it's a better
surface for a multi-field form than an Adaptive Card. Known limitation:
the magic link's embedded identity is best-effort (the case's PM, not
necessarily whoever actually clicks in a shared group chat) since Teams
doesn't personalize a card per-viewer without a real SSO round-trip — not
a security boundary regression, since every write already goes through one
shared backend service account (see web.py's auth docstring), just an
honestly-flagged gap for when per-user Teams identity matters more.

This also folded in the project-level group-chat rework from the same
ideation thread: `project_conversation_store.py` maps one conversation per
project (not per PM), seeded via `TeamsMessenger.create_group_conversation`
with every project contact, and `proactive.py` pools all of a project's
due invoices into that one conversation instead of DM'ing each PM
separately. The old per-user `conversation_store.py` was removed as dead
code once nothing needed 1:1 DM routing anymore.

⚠️ Original reprioritization note (still applies): bring over the
in-Teams reach-out UX from the earlier bot work — reminder cards asking for a
comment or a snooze, the same way stage-triggered notifications used to work
— *and* let the user have the same AI-reasoning conversation (Phase 1's agent
core, unmodified) in that identical chat. One Teams surface, two entry
points: cards for the structured ask, free text for anything else.

**Credential reality (checked, not assumed):** the in-app bot's own
registration (`TEAMS_BOT_APP_ID`) and `MICROSOFT_APP_PASSWORD` are both
unset everywhere accessible — same blocker as Azure OpenAI. Built against a
`TeamsMessenger` interface (mirrors `DocumentSource`/`DocumentIndex`'s
pattern): a `FakeMessenger` records what would have been sent so everything
below is fully testable today; a real Bot-Framework-backed implementation
drops in once the credential exists, with no call-site changes.

- **Adaptive Cards** (`app/channels/teams/cards.py`), copy and fields mirror
  the proven cards from the earlier bot (verified against real screenshots
  this session, not reinvented):
  - Reminder card: stage banner, Invoice details (invoice/amount/aging), due
    date, "Action needed" callout, Snooze + Add comment buttons.
  - Snooze form card: Reason, Resume-on date, Confirm.
  - Comment form card: Comment text, optional Expected-payment date, Save.
  - Confirmation cards for both, echoing what was recorded.
  - Invoice disambiguation card (from Phase 1's resolution rule): one
    tappable option per match, human-readable label only, invoice_id riding
    as hidden `Action.Submit` data.
- **Free-form AI chat**: any plain-text message in the same conversation
  routes straight through the *existing, unmodified* `AgentLoop` — same
  tools, same guardrails, same invoice-ID-free resolution as the web Chat
  page. Nothing Teams-specific about the reasoning; the adapter's only job is
  translating Activity in/out and rendering the answer as text or (for
  results with 2+ invoices) a disambiguation card.
- **Identity → role**: Teams AAD id resolves to `pm` (default) or `admin`
  (via `ADMIN_UPNS`) through `identity.py` — a PM can snooze/comment on
  invoices via cards or chat; only an admin gets the rest of Phase 1's tools.
- **Proactive send** (`app/channels/teams/proactive.py`, pulled forward from
  Phase 5 since it's the actual point of "sent reminders" — this doesn't
  need the rest of Phase 5's watchers, just this one path): a poller checks
  cases entering outreach stages (reminder/first/second/escalation/final),
  resolves the PM's stored Teams conversation reference
  (`conversation_store.py`, SQLite — mirrors the earlier bot's store; a PM
  must have messaged the bot once, or been installed org-wide, before a DM
  is possible — a real Teams platform constraint, not a bug), and sends the
  reminder card. Dedup key = (case_id, stage) so a nudge fires once, ever,
  per stage.
- **Accept:** with `FakeMessenger`, a seeded case reaching `first_notice`
  produces exactly one reminder-card send; tapping its Snooze button (via a
  simulated Adaptive Card submit Activity) round-trips through
  `snooze_invoice` and produces a confirmation card; a plain-text question in
  the same fake conversation gets routed through `AgentLoop` and produces a
  real, tool-grounded answer. Re-run against a real Bot Framework connection
  once `TEAMS_BOT_APP_ID`/`MICROSOFT_APP_PASSWORD` exist — no code changes
  expected, per the interface split above.

### Phase 4.5 — Manual follow-up email campaigns (added 2026-07-16)

⚠️ New feature, same day as the redirect-to-web rework above: a PM says
"follow up with the customer" (Teams chat, or the web's Follow up button)
and the agent runs a real recurring email campaign for that invoice —
customer email, cadence, optional end date, all collected on one web form
(reached the same way as snooze/comment: a magic link, or the project
invoice picker if asked from chat without naming an invoice).

**Architecture decision (confirmed with the user, not assumed):** rather
than build a whole separate email-sending + inbound-reply-capture pipeline,
this reuses Lummus's own real infrastructure. Lummus's V2 dunning engine
already sends via Microsoft Graph `/sendMail` with tracking headers
(`X-Dunning-Case-Id` etc, `backend/app/dunning_v2/actions/
internal_email_renderer.py`) and already has a Graph webhook that matches
replies back to the case by those same headers/a hidden body footer
(`backend/app/dunning_v2/inbound/detector.py`). `services/email_lineage.py`
stamps the exact same format, verified against that source. This means:
sending this app's follow-up emails through the same transport makes
Lummus's *existing* inbound webhook catch replies automatically — no new
inbound-capture mechanism needed here, and replies land in the same
`GET /cases/{id}/timeline` this app already reads (Comments section, for
free).

- **`services/email_sender.py`** — `EmailSender` interface, same swappable
  pattern as everything else blocked on a real credential
  (`FakeEmailSender` records sends; `GraphEmailSender` needs a Graph app
  registration with **Mail.Send** — a different scope than the SharePoint
  credentials, which only cover Files.Read.All — unset everywhere
  accessible, same as Azure OpenAI/Teams/Document Intelligence/Search).
- **`services/followup_store.py`** — one active campaign per invoice;
  first send happens immediately, then repeats every `cadence_days` until
  cancelled or `end_date` passes. Send history lives here locally, *not*
  on the real Lummus timeline: `POST /response-events` hardcodes its
  resulting event to "Inbound reply received" regardless of
  `source_channel`, so logging an *outbound* send through it would
  mislabel it (verified against `backend/app/dunning_v2/reviews/
  service.py`, not assumed). The web UI merges this local history into
  the Activity view client-side instead.
- **`services/followup_engine.py`** — `send_due_followups` (the sender
  poller) and `mirror_new_replies_to_teams` (watches the real timeline for
  new `reply_received` events on cases with an active campaign and posts
  them into that project's Teams chat — reuses the existing project-chat
  model from Phase 4, no new infrastructure).
- **Teams entry points**: a third `Action.OpenUrl` button ("Follow up") on
  the reminder card, and a chat-intent pattern ("follow up", "reach out",
  "chase") alongside the existing snooze/comment detection in `bot.py`,
  routed through the same project-invoice-picker redirect.
- **Accept:** verified live against real UAT data — a real campaign
  created via the web form, `send_due_followups` run manually against the
  `FakeEmailSender` produced a correctly V2-lineage-stamped email and the
  send appeared in the invoice's Activity feed; the Teams chat-intent path
  produced the correct redirect card. Real send/receive round-trip is
  blocked on the Graph Mail.Send credential, same as every other
  credential-blocked integration this session.

### Phase 4.6 — Weekly AR-health digest + a real background scheduler (added 2026-07-17)

⚠️ First of the "intelligent AI features for enterprise scale" ideation
round: a weekly digest card per project chat, AR health (open exposure,
overdue count/amount, stage mix, trend vs last week) plus a payment-pattern
projection per customer, derived from that customer's own closed-case
history.

**Projection methodology** (services/digest_engine.py's module docstring
has the full reasoning): the real backend has no dedicated "paid_at" field
on a case (verified against live UAT data), so `updated_at` is used as a
resolution-date proxy. `avg_days_relative_to_due` = mean(resolved_date -
due_date) across a customer's closed cases; sample_size is always shown
alongside it so a one-invoice history reads as low-confidence, not
false-confidence. This is an estimate stated as one, not a hidden model.

**Real gap fixed alongside this:** none of this session's proactive
pollers (send_due_reminders, send_due_followups,
mirror_new_replies_to_teams) had ever actually run unattended — they only
ran when manually invoked from a script this session. main.py now has a
real FastAPI lifespan-managed background scheduler (`_poll_loop`) that
runs each poller on its own configured interval, gated by its own
`*_POLL_ENABLED` flag (all default False so a fresh checkout does
nothing until explicitly turned on). A failing tick logs and waits for
the next one rather than killing the loop.

- **`services/digest_store.py`** — per-project snapshot (for week-over-week
  trend) + dedup so a project gets at most one digest per ISO week.
- **`services/digest_engine.py`** — `compute_ar_health`,
  `compute_customer_projection`, `send_project_digests`.
- **`channels/teams/cards.py`** — `digest_card`.
- **`main.py`** — `lifespan` context manager starts/stops the reminders,
  followups+reply-mirroring, and digest loops based on config flags.
- **Accept:** verified live that the app boots cleanly with the scheduler
  wired in (lifespan runs, no pollers start with default-False flags);
  the digest computation, card, and dedup/trend logic are covered by
  tests against realistic UAT-shaped data. Live verification against the
  real UAT backend end-to-end (a real digest card, sent for a real
  project) is pending Docker being back up — same "built and tested, real
  data verification pending" state as other work this session when the
  local stack was down.

### Phase 5 — Proactive engine (remaining watchers)
Phase 4 already covers stage-triggered reminders (the main "sent reminders"
ask). What's left here: PM hasn't replied N days after outreach; snooze
expiring in 48h; review task idle > N days. Same dedup/audit pattern as
Phase 4's poller — this phase is now small.
- **Accept:** seed each remaining scenario in the UAT stack, watcher fires
  exactly once per scenario, visible in audit log.

### Phase 6 — Meeting-join spike (timeboxed: 2 days, then stop and write up)
⚠️ Different tech entirely (Graph Cloud Communications / ACS meeting join).
Goal is a feasibility memo, not a feature: can a bot join a scheduled Teams
meeting in this tenant, receive live transcript, and post a summary card to the
meeting chat afterward? Deliverable = `docs/meeting-spike.md` with what worked,
what's blocked (permissions? media SDK? licensing?), and a go/no-go
recommendation. Do not build product code in this phase.

---

## 6. Guardrails spec (applies from Phase 1, hardened in Phase 3)

1. **Identity & roles** — allowlist in SQLite, editable at runtime by admins
   (pattern from `lummus-teams-bot/services/admin_store.py`). Roles: `admin`
   (everything), `pm` (Phase 4: read tools + exactly `snooze_invoice`/
   `resume_invoice`/`add_comment`, nothing else), `viewer` (read-only tools).
   Default admin: `viraj.yadav@corehelix.ai`. Unknown user → polite refusal,
   logged.
2. **Read/write separation** — enforced by the registry, not the prompt. A
   viewer's tool list simply doesn't contain write tools.
3. **Injection hygiene** — anything fetched from the backend (comments, emails,
   timeline text) **or extracted from a document** (Phase 1B+) is *data*. Wrap
   retrieved text in delimiters, instruct the model it's untrusted, and
   strip/neutralize instruction-like content before it enters the prompt. Test
   with a hostile comment seeded via the API, and separately with a PDF whose
   text contains an injection attempt.
4. **Confirm-before-write** — §Phase 3 protocol; no same-turn execution.
5. **Audit everything** — every tool call (read included), every refusal, every
   proactive send: append-only SQLite table with timestamp, user, channel.
6. **Blast-radius limits** — write tools operate on exactly one invoice/contact
   per call. No bulk-action tool exists, deliberately.

## 7. ⚠️ OPEN decisions (ask the user, don't assume)

1. UI auth: single shared UAT login is assumed. OK for now? — **RESOLVED:** yes.
2. Proactive channel priority: Teams DM first, or web notifications first? —
   **RESOLVED:** Teams DM first.
3. Should the Phase 6 spike happen after Phase 3 instead of last, if
   motivation is high? — **RESOLVED:** keep it last.
4. Repo hosting: push to a new private GitHub repo like `virraaaj/ar-copilot`? —
   **RESOLVED:** local git only for now.
5. Document sources beyond SharePoint + manual upload — **RESOLVED for now:**
   SharePoint + manual upload; design `DocumentSource` as pluggable so more
   can be added later without a rewrite.
6. Quote search modality — **RESOLVED:** both text description search and
   drawing/image visual similarity are in scope (drawing similarity lands in
   Phase 1D, after the text-search foundation).
7. **Still open:** Vendor Certifications field schema — what counts as
   "important information" to auto-log needs a concrete field list from the
   business before Phase 1C's extractor can be built. Placeholder assumption
   until then: vendor name, certification type, issue date, expiry date,
   certifying body, cert number.
8. **Still open:** T&Cs "standard" baseline — whose T&Cs are the reference for
   "non-standard requirement" detection, and is there a canonical current
   version to diff against? Phase 1E is blocked without this.
9. **Still open:** is this whole project still personal exploration, or does
   the "boss must-have" framing mean it's now closer to a real deliverable?
   Doesn't block building, but affects how much production-readiness (real
   auth, real hosting, error handling depth) matters before it's "done."

## 8. Definition of done

**v0.1** — Phases 0–3 complete: a web UI where you can see AR state, chat with
an agent that reasons over live data with cited tool calls, and execute
guarded write actions with confirmations and a full audit trail.

**v0.2** — Phases 1B–1E complete: the agent answers questions over all five
document types, with citations, auto-logged Vendor Cert fields queryable
directly, and drawing similarity search for quotes.

Teams (4), proactive (5), and the meeting spike (6) each cut a further minor
tag, independent of the v0.2 document work.
