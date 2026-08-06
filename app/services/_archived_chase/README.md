# Archived chase core

The chase runtime (`chase_engine`, `chase_machine`, `chase_store`,
`chase_parser`, `chase_composer`, `chase_evaluator`, `chase_trajectory`,
`chase_explain`) is superseded by `app/outcome_agent/`.

Production pollers, Teams/mail inbound, followup skip, and `/api/chases`
compat aliases now bind to CaseStore + `run_agent_tick` /
`advance_case_with_reply`.

**Kept in `app/services/`:** `chase_guardrails.py` (blackout + banned language).

Legacy modules may still exist beside this folder for older unit tests;
do not wire new code to them. Prefer `tests/outcome_agent/`.
