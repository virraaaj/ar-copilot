# Long-Horizon Outcome Agent

Principle-first collections runtime that replaces the chase engine as the
operator-facing agent. Core package: `app/outcome_agent/`. UI: `/agent`.

## Kill switches

| Flag | Default | Meaning |
|------|---------|---------|
| `OUTCOME_AGENT_ENABLED` | `false` | Background poller (`run_agent_tick`) |
| `OUTCOME_AGENT_DRY_RUN` | `true` | Outbox only — no real send |
| `OUTCOME_AGENT_TO_ADDRESS_ALLOWLIST` | empty | Extra UAT allowlist |

Legacy `CHASE_*` env names still map via a one-release shim
(`outcome_agent_enabled_effective` also honors `CHASE_ENABLED`).

## Demo path (offline, no UAT backend)

1. Copy `.env.example` → `.env`. Set placeholder `AZURE_OPENAI_*` values and
   `DEV_AUTH_BYPASS=true` (skips Lummus login verify; localhost only).
   Clear `ALLOWED_EMAIL_DOMAIN=` so any email works on the login form.
2. Start API: `python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --reload`
3. Start UI: `cd ui && npm install && npm run dev` → http://localhost:5173
   (Vite proxies `/api` → `:8090`).
4. Sign in with any email/password (e.g. `demo@corehelix.ai` / `demo`).
5. Open **Agent** in the nav → **Reset Demo** (or `POST /api/agent/demo/reset`
   with `Authorization: Bearer <session_token>`).
   - Loads six seed cases, sets sim clock to **2026-07-22**.
6. Open cockpit for **INV-4821** (`/agent/cases/<id>`).
7. Run **Scenario Runner** (`/agent/scenarios`) — packs **S1–S8**.

Manual lab controls on the cockpit: +1/+3/+7 days, jump to date (auto
process-due + tick), inject reply, post payment, create dispute, Run Agent.

## Principles → screens → scenarios

| # | Principle | Screen | Scenario |
|---|-----------|--------|----------|
| P1 | World ≠ dialogue | World vs Dialogue panel | S1, S5 |
| P2 | Commitment-centric | Commitment card | S2 |
| P3 | Bounded autonomy | Budget meters | S3 |
| P4 | Critic before act | Critic checklist | S7 |
| P5 | Judge after outcome | Post-outcome / learning | S4 |
| P6 | Memory as retrieval | Memory recall + graph | S2, S8 |
| P7 | Explicit uncertainty | Uncertainty badge | S6 |
| P8 | Hierarchical goals | Goal stack (3 rows) | S2, S6 |
| P9 | Simulate-then-act | Candidate scores table | S6 |
| P10 | Delayed credit | Learning strip / Δ weights | S4 |
| P11 | Escalation handoff | Escalation dossier | S3, S5 |
| P12 | Det. skeleton / AI flesh | Loop stepper colors | S1, S7 |
| P13 | Reflexion | Reflexion note | S4 |

## API surface

- `/api/agent/cases`, `.../events`, `.../decision-traces`, `.../memory`, budgets, goals
- `/api/agent/run-tick`, inject-reply, simulate-payment, create-dispute
- `/api/agent/demo/reset`, `/api/agent/demo/scenarios`, `.../scenarios/{id}/run`
- `/api/sim-clock/jump` — advance clock, emit due signals once, run tick
- Compat aliases: `/api/chases/*` → CaseStore / agent loop

## Tests

```bash
pytest tests/outcome_agent -q
```

Covers state machine, P1 paid-claim, budget escalation, critic regen,
jump idempotency, learning weight Δ, S1–S8 scenario packs, principle tags.

## Kept libraries

`sim_clock.py`, `graph_store.py`, `policy_knowledge.py`, and blackout/language
helpers in `chase_guardrails.py` remain the shared foundations.
