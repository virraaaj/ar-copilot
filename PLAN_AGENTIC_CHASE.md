# PLAN: Agentic Invoice Chase Engine

**Status: Phases C0-C5 implemented and live-verified (2026-07-20).**
362 tests passing. Live-verified against the real UAT backend + real
gpt-5-mini: a real overdue invoice was created (aging-table sync +
trigger-tick), the engine detected it and sent a dry-run PM outreach, and
a real LLM call correctly parsed "the customer told our AR team they will
pay this by August 15th 2026" into `commitment_date, 2026-08-15, high
confidence` and tracked the commitment. `CHASE_ENABLED`/`CHASE_DRY_RUN`
still default to `false`/`true` in `.env` -- nothing runs unattended
until a human flips those. Remaining before a real (non-dry-run,
Teams-live) demo: sideload the Teams app (task in progress, see
`infra/teams-app/`) and get the pending `Mail.Send`/`Mail.Read` admin
consents for full email-side chasing (Teams-side chasing already works
without them).

---

## 1. What the boss actually asked for

Direct quote, lightly cleaned up:

> "Something more agentic — something that requires very minimal human
> intervention. Something that basically chases an invoice until it gets a
> payment date, and if an invoice hasn't been paid by the payment date it
> got, it chases the invoice again. Sample flow: after a due date passes,
> it reaches out to the PM. Say the PM says 'oh, the customer will have an
> update' — then it reaches out to the customer until the customer can
> provide an update."

Decomposed, that is a **closed loop around a commitment**, which is a
different animal from everything built so far:

1. Today's system is *notify-and-wait*: reminder cards, digests, and
   PM-triggered follow-up emails all end with a human deciding what
   happens next.
2. The ask is *pursue-until-resolved*: the agent owns the objective
   ("get a payment date, then get the payment"), picks who to contact,
   reads the replies, extracts commitments from them, waits, verifies
   against reality, and re-engages on its own when a commitment is
   missed — escalating to a human only when it has genuinely run out of
   moves.

The unit of work is not "a reminder" — it's a **chase**: a per-invoice
pursuit with memory, a target (who we're currently chasing), a tracked
commitment (the promised date), and a bounded escalation budget.

---

## 2. The core loop (target behavior)

```
                          ┌──────────────────────────────────────────────┐
                          ▼                                              │
 due date passes ──► CHASE PM ──► PM reply ──► parse ──┬─ gave a date ──► TRACK COMMITMENT
 (invoice unpaid)      ▲  │                            │                  wait until date+grace
                       │  │ no reply after N days      │                       │
                       │  ▼                            ├─ "ask the customer" paid? ──► CLOSE ✔
                       │ re-nudge (max K), then        │        │              │
                       │ escalate to human             ▼        │           unpaid
                       │                        CHASE CUSTOMER ◄┘              │
                       │                           ▲   │                       │
                       │                           │   │ customer reply        │
                       │                           │   ▼                       │
                       │                           │  parse ──► gave a date ──►┤ (commitment #2, #3…)
                       │                           │ no reply: re-nudge (max K)│
                       │                           │                           │
                       └───────────────────────────┴──── after MAX_MISSES ────►ESCALATE TO HUMAN
                                                          missed commitments   (review task + Teams alert)
```

Two invariants that make this "agentic but safe":

- **Every cycle consumes budget.** Re-nudges per target are capped
  (`CHASE_MAX_NUDGES`, default 3), and missed commitments per invoice are
  capped (`CHASE_MAX_MISSED_COMMITMENTS`, default 3). Budget exhausted →
  human handoff, never an infinite pester loop.
- **Reality wins over replies.** "Paid" is only ever determined by the
  backend (`case_status == "closed_paid"`), never by someone *saying*
  it's paid. A "we already paid" reply transitions to a
  `verifying_payment` state that re-checks the backend for
  `CHASE_PAYMENT_VERIFY_DAYS` before treating it as a missed commitment.

---

## 3. What already exists to build on (real, verified in-repo)

| Capability | Where | State |
|---|---|---|
| Backend case data + timeline + comments | `app/services/backend_client.py` | Real, live-verified against UAT stack (`http://localhost:8001`). Payment ground truth = `case_status == "closed_paid"`. Comments land as `response-events`. |
| Real outbound email | `app/services/email_sender.py` → `GraphEmailSender` | Implemented (client-credentials + Graph `/sendMail` as `info@corehelix.ai`). **Live send currently blocked on tenant-admin consent for `Mail.Send`** — code path is done and respx-tested. |
| Email lineage headers/footer | `app/services/email_lineage.py` | Real. Stamps `X-`-prefixed headers on outbound mail. |
| Real Teams send | `app/channels/teams/messenger.py` → `BotFrameworkMessenger` | Implemented (Bot Connector REST). Needs a known `serviceUrl` per conversation, captured by the inbound edge. |
| Teams inbound edge | `app/channels/teams/http.py` (`POST /api/messages`) + `auth.py` (real RS256 JWT validation) | Implemented + tested. Live path needs the devtunnel running and the Teams app sideloaded (in progress, task #57). |
| Agent loop + tool registry + role guardrails | `app/agent/loop.py`, `registry.py`, `setup.py`, `app/guardrails/identity.py` | Real, with live gpt-5-mini (`app/services/azure_openai.py`). |
| Background scheduler | `app/main.py` `_poll_loop` + `lifespan`, per-poller `*_POLL_ENABLED` flags | Real. This is the pattern every new poller must follow. |
| Dumb cadence follow-ups | `app/services/followup_store.py` + `followup_engine.py` | Real. Fixed-cadence emailing with no reply understanding. **The chase engine supersedes this** (see §8 migration note). |
| SQLite store pattern | `followup_store.py`, `digest_store.py`, `project_conversation_store.py` | aiosqlite against `STATE_DB_PATH`, schema-on-first-use, plain dict rows. Copy this pattern. |
| Project ↔ Teams chat mapping | `project_conversation_store.py` | Real. How the chase engine finds "the PM's chat" for a project. |
| Magic links to web | `app/guardrails/magic_link.py` | Real. Use for "review this chase" links in Teams alerts. |

### Known constraint that shapes the whole design: inbound email

Outbound email sends from a **new app registration in the corehehelix.ai
tenant** (`GRAPH_MAIL_*` in `.env`), *not* the mailbox Lummus's own inbound
webhook watches. **Customer email replies are NOT automatically captured
today.** The app has `Mail.Send` only (deliberately least-privilege).

Consequences for this plan:

- **PM-side conversation should ride on Teams**, where inbound already
  works end-to-end through `/api/messages`. Email to PMs is a fallback
  notification, not the reply channel.
- **Customer-side replies need a new inbound mechanism** (Phase C3):
  poll the `info@corehelix.ai` inbox via Graph. That requires adding
  `Mail.Read` (Application) to the ar-copilot app registration — and it
  **must** be paired with an Exchange **Application Access Policy**
  scoping the app to only the `info@` mailbox, since app-level
  `Mail.Read` is otherwise tenant-wide. This is an admin ask; batch it
  with the pending `Mail.Send` consent ask so the admin is bothered once.
- Reply↔chase matching: stamp every outbound chase email's **subject**
  with a short token like `[AR-7Q2F]` (store it on the chase row) and
  also stamp `X-ARC-Chase-Id` via the existing lineage-header mechanism.
  Match inbound on the subject token first (survives every mail client),
  headers as a bonus. Do not rely on `In-Reply-To` alone.

---

## 4. Design

### 4.1 Chase state machine

One chase per backend case (invoice). States:

| State | Meaning | Leaves when |
|---|---|---|
| `pending` | Due date passed, unpaid, chase created, first outreach not yet sent | Poller sends PM outreach → `awaiting_pm` |
| `awaiting_pm` | Asked the PM, waiting for their reply | Reply parsed → per outcome; or nudge budget exhausted → `escalated` |
| `awaiting_customer` | Asked the customer (PM punted, or PM told us to), waiting | Reply parsed → per outcome; or nudge budget exhausted → `escalated` |
| `commitment_tracked` | Have a promised payment date; quiet until then | Date + `CHASE_GRACE_DAYS` passes → paid? `closed_paid` : back to chasing whoever made the commitment (`awaiting_pm`/`awaiting_customer`), `missed_count += 1` |
| `verifying_payment` | Someone claims it's paid; backend doesn't show it yet | Backend shows paid → `closed_paid`; `CHASE_PAYMENT_VERIFY_DAYS` elapse unpaid → treat as missed commitment |
| `escalated` | Out of moves; human owns it now | Human resolves/closes in UI, or backend shows paid |
| `paused` | Human clicked pause (or invoice snoozed via existing snooze feature) | Human resumes; auto-resume respects backend pause state |
| `closed_paid` | Backend confirms payment | Terminal |
| `closed_manual` | Human closed the chase (dispute, write-off, etc.) | Terminal |

Rules the implementation must enforce:

- **Idempotent creation**: the poller may see the same overdue case on
  every tick; `(case_id)` is unique on the chases table — create only if
  no non-terminal chase exists.
- **Snooze integration**: if the backend case has an active pause
  (`active_pause_id` non-null), the chase auto-holds — the existing
  snooze feature must keep working as the human's "leave it alone" lever.
- **Every transition is appended** to a `chase_events` log table (who/
  what/why/LLM confidence when relevant) AND mirrored as a backend
  timeline comment via the existing `add_comment` path
  (`source_channel="manual_only"`, prefix `[ar-copilot chase]`) so the
  chase's story is visible on the real invoice timeline, not just in a
  local DB.

### 4.2 Commitment parsing (the genuinely-LLM part)

New module `app/services/chase_parser.py`. One function:

```python
async def parse_chase_reply(llm, reply_text: str, chase_context: dict) -> ParsedReply
```

Uses the existing `AzureOpenAIService.chat()` with a **forced tool call**
(`tool_choice` on a single `record_reply_interpretation` function schema)
so output is structured, never free text. `ParsedReply`:

- `intent`: one of `commitment_date` | `handoff_to_customer` |
  `claims_paid` | `dispute` | `no_commitment` (e.g. "let me check") |
  `unclear`
- `promised_date`: ISO date or null. The prompt must give today's date
  and require **absolute** date resolution ("Friday" → an actual date);
  vague quantities ("soon", "next month" with no day) → `no_commitment`,
  not a guessed date.
- `customer_contact_email`: email or null (PMs often reply "chase
  bob@customer.com" — capture it; validate with the existing
  `check_valid_email` guardrail before use).
- `confidence`: `high` | `low`.

Dispatch rules (in `chase_engine`, not the parser):

- `high` + `commitment_date` → track it, confirm back to the sender
  ("Got it — expecting payment by {date}. I'll check back then.").
- `high` + `handoff_to_customer` → switch target to customer (see §4.3
  for where the address comes from).
- `claims_paid` → `verifying_payment`.
- `dispute` → immediate `escalated` (never argue a dispute with a bot).
- `no_commitment`/`unclear` or any `low` → **one** clarifying question
  ("Thanks — is there a specific date I should expect payment by?"), and
  if the next reply still doesn't parse `high`, escalate. Never loop
  clarifications.

Past-date sanity: a parsed `promised_date` in the past → treat as
`unclear`. Date > `CHASE_MAX_COMMITMENT_DAYS` (default 90) out →
escalate for human review instead of silently accepting a stall.

### 4.3 Who gets contacted, on what channel

- **PM**: from the project's contacts (`backend.list_project_contacts`,
  `contact_type == "pm"`, falling back to `general_manager`). Channel:
  the project's Teams group chat if one exists in
  `ProjectConversationStore` (replies then arrive through the existing
  `/api/messages` edge); otherwise email via `GraphEmailSender` with the
  subject token.
- **Customer**: email only. Address sources, in order: (1) explicit
  address parsed out of a PM reply, (2) an active follow-up campaign's
  `customer_email` for the same case (`FollowUpStore`), (3) none → ask
  the PM for it (that ask is itself a chase message), and if the PM
  doesn't provide one within the nudge budget → escalate. **Never guess
  or synthesize a customer email address.**
- Every outbound message states plainly it's from an automated AR
  assistant and that replying to it works.

### 4.4 Routing inbound to the right chase

- **Teams**: `/api/messages` currently routes every plain-text message to
  `TeamsBot._handle_text` → AgentLoop. Extend: before the agent-loop
  fallback, check whether this conversation's project has a chase in
  `awaiting_pm`/`awaiting_customer`/clarifying state whose last outreach
  went to this chat. If yes, run the message through `parse_chase_reply`
  and dispatch; only fall through to normal Q&A when the parse says the
  message clearly isn't about the chase (`unclear` on a chat with no
  pending chase question, etc.). One project chat can hold multiple open
  chases — when ambiguous, ask which invoice ("Is that about MRD-PRE-2601
  or MRD-FST-2601?") using the same disambiguation-card pattern
  `bot.py` already has.
- **Email** (Phase C3): `chase_mail_poller` reads the `info@` inbox via
  Graph (`GET /users/{mailbox}/mailFolders/inbox/messages`, filter on
  receivedDateTime > watermark), matches the subject token to a chase,
  parses, dispatches, and marks the message-id as processed in the store
  (dedupe table — Graph reads are at-least-once).

### 4.5 Escalation surface (the "minimal, not zero, intervention" part)

When a chase hits `escalated`:

1. Post a Teams alert card to the project chat (or DM/email the PM if no
   chat): what was promised, by whom, how many misses, full chase
   history, with a magic-link button to the web review page.
2. Create a review row visible in a new **Chases** tab in the web UI:
   table of all chases (state, target, promised date, misses, last
   activity), with actions: pause/resume, close-manual (with reason),
   edit commitment date, restart chase, view event log. This tab is also
   how you demo the whole feature to the boss.
3. `escalated` chases send **nothing further** automatically.

### 4.6 Global guardrails

- `CHASE_ENABLED` master flag (default `false`) — the kill switch; the
  poller doesn't even start without it. Per-run cap
  `CHASE_MAX_SENDS_PER_TICK` (default 10) so a bug or a cold-start
  backlog can't mass-email.
- Per-target rate limit: never message the same person about the same
  invoice more than once per `CHASE_MIN_HOURS_BETWEEN_TOUCHES` (default
  72h) except direct replies to something they just sent.
- `CHASE_DRY_RUN` flag: full state machine + parsing runs, outbound
  sends are logged to `chase_events` instead of sent. Ship with dry-run
  `true`; the demo to the boss can literally be "watch the event log
  decide," then flip it live.
- Optional allowlist `CHASE_TO_ADDRESS_ALLOWLIST` (comma-separated): if
  non-empty, outbound email only goes to listed addresses — for UAT, so
  the "customer" is always a test mailbox.

---

## 5. Data model (new tables in `STATE_DB_PATH`, aiosqlite, follow `followup_store.py`'s conventions)

```sql
CREATE TABLE IF NOT EXISTS chases (
    id TEXT PRIMARY KEY,              -- uuid
    case_id TEXT NOT NULL,            -- backend case UUID
    case_key TEXT,                    -- e.g. V2-AUTO-...  (display/debug)
    invoice_no TEXT,                  -- primary_invoice_id (human-facing)
    project_number TEXT,
    subject_token TEXT NOT NULL,      -- e.g. AR-7Q2F, unique, stamped into email subjects
    state TEXT NOT NULL,              -- §4.1 states
    target TEXT,                      -- 'pm' | 'customer' | NULL
    pm_email TEXT,
    customer_email TEXT,
    promised_date TEXT,               -- ISO date of current commitment
    promised_by TEXT,                 -- 'pm' | 'customer'
    missed_count INTEGER NOT NULL DEFAULT 0,
    nudge_count INTEGER NOT NULL DEFAULT 0,   -- resets on state change
    clarify_count INTEGER NOT NULL DEFAULT 0, -- resets on state change
    last_outreach_at TEXT,
    next_action_at TEXT,              -- when the poller should look at this chase again
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chases_open_case
    ON chases(case_id) WHERE state NOT IN ('closed_paid','closed_manual');

CREATE TABLE IF NOT EXISTS chase_events (
    id TEXT PRIMARY KEY,
    chase_id TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,               -- created|outreach_sent|reply_received|parsed|
                                      -- commitment_tracked|commitment_missed|state_change|
                                      -- escalated|human_action|dry_run_send|error
    detail TEXT                       -- JSON blob: message text, parse result, confidence, channel…
);

CREATE TABLE IF NOT EXISTS chase_processed_mail (
    message_id TEXT PRIMARY KEY,      -- Graph message id, inbound dedupe
    chase_id TEXT,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

`next_action_at` is the heart of the poller: each tick selects chases
where `next_action_at <= now` (plus scans for new overdue cases), acts,
and writes the new `next_action_at`. No per-chase timers, no cron
sprawl — one poller, DB-driven scheduling, matching the existing
architecture.

---

## 6. Implementation phases

Each phase = code + tests green (`.venv/Scripts/python.exe -m pytest
tests/ -q`; suite is currently 257 passing — keep it green) + a commit.
Follow the repo's established test idioms: respx for HTTP, `ScriptedLLM`
for the model, temp-SQLite stores, `FakeMessenger`/`FakeEmailSender`,
monkeypatched `Settings`.

### Phase C0 — store + state machine (no I/O)
`app/services/chase_store.py` (tables above, CRUD, `list_due(now)`),
`app/services/chase_machine.py` (pure transition function:
`(chase, event) -> (new_state, actions)` where actions are declarative
like `SendToPM(text)`, `Escalate(reason)` — the engine executes them).
Pure logic → exhaustively unit-test every transition, budget cap, and
idempotency rule from §4.1. This is the phase to get *right*.

### Phase C1 — commitment parser
`app/services/chase_parser.py` per §4.2. Tests with `ScriptedLLM`
covering each intent, relative-date resolution, past-date and >90-day
sanity rules, malformed tool-call output (treat as `unclear`), and one
live smoke test against real gpt-5-mini (mark it skip-unless-env, like
nothing else in the suite hits the live model by default).

### Phase C2 — chase engine + poller
`app/services/chase_engine.py`: `run_chase_tick(backend, messenger,
email_sender, chase_store, project_conversation_store, llm) -> int`.
Detect newly-overdue unpaid cases (`list_cases`, due date past, not
`closed_paid`, no active pause) → create chases; process due chases;
execute actions (Teams first, email fallback per §4.3); message
templates in `app/services/chase_messages.py` (subject token + lineage
headers on email); mirror everything to the backend timeline; honor
`CHASE_DRY_RUN`, allowlist, and per-tick cap. Wire into `app/main.py`
lifespan as `_poll_loop("chase", CHASE_POLL_INTERVAL_SECONDS, …)` gated
on `CHASE_ENABLED`. New settings in `app/config.py` (all defaults per
§4.6, `CHASE_POLL_INTERVAL_SECONDS` default 900).

### Phase C3 — inbound replies
Teams: the pre-AgentLoop chase check in `TeamsBot._handle_text`
(§4.4), including the multi-chase disambiguation path. Email:
`chase_mail_poller` in `chase_engine.py` (Graph inbox read + subject-token
match + dedupe), gated on its own `CHASE_MAIL_POLL_ENABLED` since it
needs the `Mail.Read` admin consent that doesn't exist yet — the Teams
path must work without it. Extend `GraphEmailSender`'s module or a small
`graph_mailbox.py` with the read call (same token-acquisition code,
different scope usage).

### Phase C4 — escalation surface + web UI
Escalation card in `app/channels/teams/cards.py` (+ magic-link action
`review_chase`); web endpoints in `app/channels/web.py`
(`GET /api/chases`, `GET /api/chases/{id}/events`, `POST
/api/chases/{id}/pause|resume|close|restart`, `PATCH …/commitment`);
`ui/src/pages/Chases.tsx` + route + NavBar link (match ARHealth.tsx's
styling); every human action writes a `human_action` chase event.
Also register `get_chase_status` as a read tool in `app/agent/tools_read.py`
so the chat can answer "what's happening with invoice X" from chase state.

### Phase C5 — verify live + docs
End-to-end against the UAT stack with `CHASE_DRY_RUN=true`: seed an
overdue invoice (the `UAT_Automation/data/generate_uat_round_files.py`
files work for this), watch the full loop in `chase_events` (create →
PM outreach → scripted PM reply through the real Teams edge or a direct
`/api/messages` POST → commitment tracked → simulate date passing →
miss → customer chase → escalate). Then a controlled non-dry-run of one
Teams message. Update `PLAN.md` §5 with a Phase 6 section pointing here;
update this file's status line.

---

## 7. Acceptance criteria (demo script for the boss)

1. Upload an aging file that makes an invoice overdue. Within one poll
   tick, the PM's project chat gets: *"Invoice X for {project} is N days
   overdue (${amount}). Is there a payment date I should track?"* — no
   human triggered it.
2. PM replies "customer said end of next week." The agent replies
   confirming the specific resolved date. `chases` row shows
   `commitment_tracked`, correct absolute date.
3. PM instead replies "you'd have to ask the customer, bob@x.com" → the
   customer address gets a chase email (or a dry-run event), state
   `awaiting_customer`.
4. Promised date passes, invoice still unpaid → re-chase fires at
   whoever committed, `missed_count` = 1, no human involved.
5. Third miss → Teams escalation card + the chase shows in the web
   Chases tab as `escalated`, and the agent goes quiet on it.
6. At any point, invoice paid in the backend → chase closes itself
   (`closed_paid`) and sends nothing further.
7. Chat: "what's the status of invoice X?" → answer includes the live
   chase state and promised date.

---

## 8. Open items & decisions already made

**Decided (don't relitigate):**
- Paid = backend `closed_paid`, nothing else.
- Disputes and >90-day promises escalate immediately.
- Ship dry-run-first, master kill switch, hard budgets. Boundless
  autonomy is a bug, not the feature.
- Chase engine supersedes the fixed-cadence follow-up feature for chased
  invoices: don't delete `followup_engine.py`, but a case with an open
  chase must not also get cadence emails — add that guard to
  `send_due_followups` (skip cases with a non-terminal chase).

**Needs the user/boss (implementing agent: ask, don't assume):**
1. Admin consents pending in the corehelix tenant: `Mail.Send` (already
   requested, blocks all real email) and now `Mail.Read` + Application
   Access Policy scoped to `info@corehelix.ai` (blocks Phase C3 email
   inbound). Teams-only chasing works without either.
2. Real customer email flow: is there a customer-contact source in
   Lummus we haven't found, or is PM-provides-it (§4.3) the permanent
   answer?
3. Cadence numbers (nudge spacing, grace days, miss cap) — defaults
   above are sensible; confirm with the boss before going non-dry-run.
4. Should the *first* touch on an overdue invoice go to the PM (current
   design, matches the boss's sample flow) or straight to the customer
   for small/low-risk invoices? Default: always PM first.
