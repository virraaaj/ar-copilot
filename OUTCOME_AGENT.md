# Long-Horizon Outcome Agent — Trace Studio

Invoice follow-up agent with a **local mailbox**, **Azure OpenAI forced tool-calls**,
and a **step-by-step Trace** of every read/write/LLM/mail action.

## Quick demo

1. Ensure `.env` has working `AZURE_OPENAI_*` (or set `OUTCOME_AGENT_LLM_MODE=mock`).
2. API: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --reload`
3. UI: `cd ui && npm run dev` → http://localhost:5173
4. Login (`DEV_AUTH_BYPASS=true`: any email/password).
5. **Agent → Reset Demo**
6. Open **INV-7104** (or INV-4821) → **Run follow-up**
7. Inspect **Live Trace** steps (memory → plan → draft → judge → mail.send)
8. Type a customer reply in the thread → **Send reply & run agent**
9. Or browse **Mailbox** at `/agent/mailbox`

## What each Trace step shows

| Phase | Meaning |
|-------|---------|
| `signal` | Why the run started |
| `memory.read.*` | Operational / episodic / semantic / document / graph |
| `context.built` | Context packet given to the planner |
| `llm.plan` / `llm.draft` / `llm.judge` | Forced tool-call payloads + tokens |
| `mail.send` / `mail.receive` | Local mailbox message |
| `memory.write` / `graph.write` | Persist facts & edges |
| `state.transition` / `schedule` | Case state + next wake-up |

Click any step → **Reads / Writes / Call** JSON in the detail panel.

## Kill switches

| Flag | Default | Meaning |
|------|---------|---------|
| `OUTCOME_AGENT_ENABLED` | `false` | Background poller |
| `OUTCOME_AGENT_DRY_RUN` | `true` | Still writes local mailbox; no external SMTP |
| `OUTCOME_AGENT_LLM_MODE` | `live` | `live` = Azure OpenAI; `mock` = deterministic/CI |

## API (new)

- `POST /api/agent/cases/{id}/run-follow-up`
- `POST /api/agent/cases/{id}/mailbox-reply`
- `GET /api/agent/cases/{id}/traces`
- `GET /api/agent/traces/{run_id}`
- `GET /api/agent/mailbox`

## Tests

```bash
pytest tests/outcome_agent -q
```

Includes golden path: follow-up phases, mailbox reply, ScriptedLLM tools, critic blocks threats.

## Look & feel

Trace Studio / Mailbox use a **Lummus** dark theme (brand green, black canvas,
mono payloads) ported from `lummus_scale`.
