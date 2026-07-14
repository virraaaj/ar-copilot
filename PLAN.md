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
  dedupe). Not Postgres — this project owns no business data.
- **pytest + respx** for tests (mock the backend API; no live calls in CI).

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
  ui/                        ← Vite app
    src/pages/Dashboard.tsx
    src/pages/InvoiceDetail.tsx
    src/pages/Chat.tsx
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
- CLI harness (`python -m app.cli "which invoices are worst?"`) for fast
  iteration without Teams or the UI.
- **Accept:** it answers multi-step questions that require chaining tools, e.g.
  "which project has the most money stuck past 60 days, and has its PM ever
  replied?" — verified against the UAT stack's seeded data. It refuses
  action requests with a clear "I can look things up but can't act yet."

### Phase 2 — Minimal web UI
Three pages, deliberately small:
- **Dashboard** — aging summary tiles (total open, by bucket), invoice table
  (status/stage filters), snoozed list. Read-only.
- **Invoice detail** — timeline, contacts, comments for one invoice.
- **Chat** — the same agent, streamed (SSE). This is the primary iteration
  surface for agent quality from here on.
- Login: reuse the backend's JWT login; store token in memory (this is a dev
  tool, not production auth).
- **Accept:** `npm run build` output served by FastAPI; all three pages work
  against the UAT stack.

### Phase 3 — Write actions with confirm-before-execute
- Write tools: `snooze_invoice`, `resume_invoice`, `close_invoice`,
  `add_comment`, `update_project_contact`, `trigger_outreach`.
- Two-step protocol: agent proposes → returns a structured confirmation
  (action, target, params, consequence) → user confirms in UI/chat → only then
  execute. Never execute a write in the same turn it was proposed.
- Every executed write goes to the audit log (who, what, when, tool args,
  backend response).
- Rules engine in `policy.py`: e.g. snooze on Pre-Due → refuse with the reason;
  close requires a reason string; contact edits echo the before/after.
- **Accept:** "snooze INV-X for 2 weeks, dispute" round-trips with confirmation;
  audit row exists; refusals tested for each policy rule.

### Phase 4 — Teams channel adapter
- Bot Framework messaging endpoint reusing `lummus-teams-bot`'s app
  registration pattern (`appPackage/`, `deploy/azure-deploy.md` there document
  the setup — follow them rather than re-deriving).
- Adaptive Cards for confirmations (Confirm/Cancel buttons) and invoice
  summaries; plain markdown for answers.
- Same guardrails: Teams AAD identity → `identity.py` → role.
- **Accept:** the Phase 1/3 test conversations work in a real Teams 1:1 chat.

### Phase 5 — Proactive engine
- `watchers.py` polling loop (start simple; no queues): detects — new invoice
  entered Escalation/Final; PM hasn't replied N days after outreach; snooze
  expiring in 48h; review task idle > N days.
- Each finding → a proactive Teams message (or web notification) with a dedupe
  key in SQLite (one nudge per finding, ever, unless state regresses).
- **Accept:** seed a scenario in the UAT stack, watcher fires exactly once,
  visible in audit log.

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
   (everything), `viewer` (read-only tools). Default admin:
   `viraj.yadav@corehelix.ai`. Unknown user → polite refusal, logged.
2. **Read/write separation** — enforced by the registry, not the prompt. A
   viewer's tool list simply doesn't contain write tools.
3. **Injection hygiene** — anything fetched from the backend (comments, emails,
   timeline text) is *data*. Wrap retrieved text in delimiters, instruct the
   model it's untrusted, and strip/neutralize instruction-like content before
   it enters the prompt. Test with a hostile comment seeded via the API.
4. **Confirm-before-write** — §Phase 3 protocol; no same-turn execution.
5. **Audit everything** — every tool call (read included), every refusal, every
   proactive send: append-only SQLite table with timestamp, user, channel.
6. **Blast-radius limits** — write tools operate on exactly one invoice/contact
   per call. No bulk-action tool exists, deliberately.

## 7. ⚠️ OPEN decisions (ask the user, don't assume)

1. UI auth: single shared UAT login is assumed. OK for now?
2. Proactive channel priority: Teams DM first, or web notifications first?
3. Should the Phase 6 spike happen after Phase 3 instead of last, if
   motivation is high? (It's independent of Phases 4–5.)
4. Repo hosting: push to a new private GitHub repo like `virraaaj/ar-copilot`?

## 8. Definition of done (v0.1 tag)

Phases 0–3 complete: a web UI where you can see AR state, chat with an agent
that reasons over live data with cited tool calls, and execute guarded write
actions with confirmations and a full audit trail. Teams (4), proactive (5),
and the meeting spike (6) each cut a further minor tag.
