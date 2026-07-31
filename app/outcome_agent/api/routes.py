"""FastAPI routes under /api/agent (+ /api/chases compat aliases)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.config import get_settings
from app.outcome_agent.config.collections_outcome import outcome_definition
from app.outcome_agent.config.policies import policy_from_settings
from app.outcome_agent.config.seed.scenarios import list_scenarios
from app.outcome_agent.loop.explain import explain_event
from app.outcome_agent.loop.scheduler import (
    advance_case_with_reply,
    create_dispute_for_case,
    jump_sim_clock,
    reset_demo,
    run_agent_tick,
    run_scenario,
    simulate_payment_for_case,
)
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.event_ledger import EventLedger
from app.outcome_agent.store.learning_store import LearningStore
from app.services import sim_clock

# require_session injected from web.py when included

# Body models MUST live at module scope. With `from __future__ import annotations`,
# FastAPI resolves annotations via get_type_hints(globalns=module); nested classes
# inside build_agent_router() stay unresolved strings, so parameters named `body`
# are treated as required *query* params → 422 {"loc":["query","body"]}.


class ReplyBody(BaseModel):
    text: str


class NoteBody(BaseModel):
    note: str = ""


class JumpBody(BaseModel):
    date: str


def build_agent_router(require_session) -> APIRouter:
    router = APIRouter(tags=["outcome-agent"])

    def _store() -> CaseStore:
        return CaseStore()

    @router.get("/agent/cases")
    async def list_cases(
        state: Optional[str] = None,
        case_id: Optional[str] = None,
        _user: str = Depends(require_session),
    ) -> List[Dict[str, Any]]:
        return await _store().list_all(state=state, case_id=case_id)

    @router.get("/agent/cases/{case_row_id}")
    async def get_case(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        case = await _store().get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        return case

    @router.get("/agent/cases/{case_row_id}/events")
    async def case_events(case_row_id: str, _user: str = Depends(require_session)) -> List[Dict[str, Any]]:
        store = _store()
        case = await store.get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        ledger = EventLedger(db_path=store.db_path)
        events = await ledger.list_for_case(case_row_id)
        for e in events:
            e["explanation"] = explain_event(case, e)
        return events

    @router.get("/agent/cases/{case_row_id}/decision-traces")
    async def decision_traces(case_row_id: str, _user: str = Depends(require_session)) -> List[Dict[str, Any]]:
        store = _store()
        if not await store.get(case_row_id):
            raise HTTPException(404, "Case not found")
        return await store.list_decision_traces(case_row_id)

    @router.get("/agent/cases/{case_row_id}/graph")
    async def case_graph(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        store = _store()
        case = await store.get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        from app.services.graph_store import GraphStore

        g = GraphStore(db_path=store.db_path)
        return await g.neighborhood(f"invoice:{case.get('invoice_no')}", active_only=False)

    @router.get("/agent/cases/{case_row_id}/memory")
    async def case_memory(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        store = _store()
        case = await store.get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        from app.outcome_agent.memory.context_builder import build_context_packet
        from app.services.graph_store import GraphStore

        ledger = EventLedger(db_path=store.db_path)
        learning = LearningStore(db_path=store.db_path)
        events = await ledger.list_for_case(case_row_id)
        weights = await learning.tactic_weights()
        packet = await build_context_packet(
            case,
            events,
            tactic_weights=weights,
            graph_store=GraphStore(db_path=store.db_path),
            policy_config=policy_from_settings(get_settings()),
        )
        return {
            "memory_facts": packet.get("recalled_memory_facts"),
            "context_packet": packet,
        }

    @router.get("/agent/cases/{case_row_id}/budgets")
    async def case_budgets(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        case = await _store().get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        return case.get("budget") or {}

    @router.get("/agent/cases/{case_row_id}/goals")
    async def case_goals(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        case = await _store().get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        return case.get("goals") or {}

    @router.post("/agent/cases/{case_row_id}/pause")
    async def pause_case(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        store = _store()
        case = await store.get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        await store.update(case_row_id, state="paused", paused=True)
        return {"ok": True, "state": "paused"}

    @router.post("/agent/cases/{case_row_id}/resume")
    async def resume_case(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        store = _store()
        case = await store.get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        await store.update(case_row_id, state="outreach_ready", paused=False)
        return {"ok": True, "state": "outreach_ready"}

    @router.post("/agent/cases/{case_row_id}/close")
    async def close_case(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        store = _store()
        if not await store.get(case_row_id):
            raise HTTPException(404, "Case not found")
        await store.update(case_row_id, state="closed", next_action_at=None)
        return {"ok": True, "state": "closed"}

    @router.post("/agent/cases/{case_row_id}/restart")
    async def restart_case(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        store = _store()
        case = await store.get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        await store.update(
            case_row_id,
            state="overdue",
            next_action_at=(await sim_clock.now(store.db_path)).isoformat(),
            paused=False,
        )
        return {"ok": True, "state": "overdue"}

    @router.post("/agent/run-tick")
    async def run_tick(_user: str = Depends(require_session)) -> Dict[str, Any]:
        s = get_settings()
        # Manual tick always works (demo) even if poller flag is off
        return await run_agent_tick(s)

    @router.post("/agent/cases/{case_row_id}/inject-reply")
    async def inject_reply(
        case_row_id: str,
        body: ReplyBody = Body(...),
        _user: str = Depends(require_session),
    ) -> Dict[str, Any]:
        try:
            return await advance_case_with_reply(case_row_id, body.text, get_settings())
        except KeyError:
            raise HTTPException(404, "Case not found")

    @router.post("/agent/cases/{case_row_id}/simulate-payment")
    async def sim_pay(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        try:
            return await simulate_payment_for_case(case_row_id, get_settings())
        except KeyError:
            raise HTTPException(404, "Case not found")

    @router.post("/agent/cases/{case_row_id}/create-dispute")
    async def create_dispute(
        case_row_id: str,
        body: NoteBody = Body(...),
        _user: str = Depends(require_session),
    ) -> Dict[str, Any]:
        try:
            return await create_dispute_for_case(case_row_id, get_settings(), note=body.note)
        except KeyError:
            raise HTTPException(404, "Case not found")

    @router.post("/agent/cases/{case_row_id}/add-note")
    async def add_note(
        case_row_id: str,
        body: NoteBody = Body(...),
        _user: str = Depends(require_session),
    ) -> Dict[str, Any]:
        store = _store()
        case = await store.get(case_row_id)
        if not case:
            raise HTTPException(404, "Case not found")
        ledger = EventLedger(db_path=store.db_path)
        await ledger.append(case_row_id, "note", {"note": body.note})
        return {"ok": True}

    @router.post("/agent/demo/reset")
    async def demo_reset(_user: str = Depends(require_session)) -> Dict[str, Any]:
        return await reset_demo()

    @router.get("/agent/demo/scenarios")
    async def demo_scenarios(_user: str = Depends(require_session)) -> List[Dict[str, Any]]:
        return list_scenarios()

    @router.post("/agent/demo/scenarios/{scenario_id}/run")
    async def demo_run_scenario(
        scenario_id: str, _user: str = Depends(require_session)
    ) -> Dict[str, Any]:
        try:
            return await run_scenario(scenario_id, get_settings())
        except KeyError:
            raise HTTPException(404, f"Unknown scenario {scenario_id}")

    @router.get("/agent/learning/summary")
    async def learning_summary(_user: str = Depends(require_session)) -> Dict[str, Any]:
        return await LearningStore().summary()

    @router.get("/agent/commitment-metric")
    async def commitment_metric(_user: str = Depends(require_session)) -> Dict[str, Any]:
        from app.outcome_agent.metrics import compute_commitment_metric

        return (await compute_commitment_metric(CaseStore())).to_dict()

    @router.get("/agent/outbox")
    async def agent_outbox(_user: str = Depends(require_session)) -> List[Dict[str, Any]]:
        return await _store().list_outbox()

    @router.post("/agent/cases/{case_row_id}/run-follow-up")
    async def run_follow_up(case_row_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        from app.outcome_agent.loop.traced_loop import run_traced_follow_up

        try:
            return await run_traced_follow_up(case_row_id, trigger="manual_follow_up", settings=get_settings())
        except ValueError:
            raise HTTPException(404, "Case not found")

    @router.get("/agent/cases/{case_row_id}/traces")
    async def list_traces(case_row_id: str, _user: str = Depends(require_session)) -> List[Dict[str, Any]]:
        from app.outcome_agent.store.trace_store import TraceStore

        store = _store()
        if not await store.get(case_row_id):
            raise HTTPException(404, "Case not found")
        return await TraceStore(db_path=store.db_path).list_runs_for_case(case_row_id)

    @router.get("/agent/traces/{run_id}")
    async def get_trace(run_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        from app.outcome_agent.store.trace_store import TraceStore

        run = await TraceStore().get_run(run_id)
        if not run:
            raise HTTPException(404, "Trace run not found")
        return run

    @router.get("/agent/mailbox")
    async def mailbox_list(
        direction: Optional[str] = None,
        case_id: Optional[str] = None,
        _user: str = Depends(require_session),
    ) -> List[Dict[str, Any]]:
        from app.outcome_agent.mailbox.store import MailboxStore

        mb = MailboxStore()
        if case_id:
            return await mb.list_for_case(case_id)
        return await mb.list_all(direction=direction)

    @router.get("/agent/mailbox/{message_id}")
    async def mailbox_get(message_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        from app.outcome_agent.mailbox.store import MailboxStore

        msg = await MailboxStore().get(message_id)
        if not msg:
            raise HTTPException(404, "Message not found")
        return msg

    @router.post("/agent/cases/{case_row_id}/mailbox-reply")
    async def mailbox_reply(
        case_row_id: str,
        body: ReplyBody = Body(...),
        _user: str = Depends(require_session),
    ) -> Dict[str, Any]:
        from app.outcome_agent.loop.traced_loop import submit_customer_reply

        try:
            return await submit_customer_reply(case_row_id, body.text, settings=get_settings())
        except ValueError:
            raise HTTPException(404, "Case not found")

    @router.get("/agent/outcome-definition")
    async def agent_outcome_def(_user: str = Depends(require_session)) -> Dict[str, Any]:
        return outcome_definition(policy_from_settings(get_settings()))

    @router.get("/agent/policy-config")
    async def agent_policy(_user: str = Depends(require_session)) -> Dict[str, Any]:
        s = get_settings()
        p = policy_from_settings(s)
        return {
            "outcome_agent_enabled": s.OUTCOME_AGENT_ENABLED,
            "outcome_agent_enabled_effective": s.outcome_agent_enabled_effective,
            "dry_run": p.dry_run,
            "budgets": {
                "max_unanswered": p.max_unanswered,
                "max_missed_promises": p.max_missed_promises,
                "max_postponements": p.max_postponements,
                "min_days_between_emails": p.min_days_between_emails,
            },
            "to_address_allowlist": p.to_address_allowlist,
            "max_sends_per_tick": p.max_sends_per_tick,
        }

    # ---- sim-clock jump (also mounted at /sim-clock/jump from web) ----
    @router.post("/sim-clock/jump")
    async def sim_clock_jump(
        body: JumpBody = Body(...),
        _user: str = Depends(require_session),
    ) -> Dict[str, Any]:
        result = await jump_sim_clock(body.date, get_settings())
        return {
            "simulated_at": result["now"],
            "is_simulated": True,
            "tick": result["tick"],
        }

    # ---- Compat /chases aliases ----
    @router.get("/chases")
    async def chases_list(
        state: Optional[str] = None,
        case_id: Optional[str] = None,
        _user: str = Depends(require_session),
    ) -> List[Dict[str, Any]]:
        cases = await _store().list_all(state=state, case_id=case_id)
        return [_compat_chase(c) for c in cases]

    @router.get("/chases/commitment-metric")
    async def chases_metric(_user: str = Depends(require_session)) -> Dict[str, Any]:
        from app.outcome_agent.metrics import compute_commitment_metric

        return (await compute_commitment_metric(CaseStore())).to_dict()

    @router.get("/chases/{chase_id}")
    async def chases_get(chase_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        case = await _store().get(chase_id)
        if not case:
            raise HTTPException(404, "Chase not found")
        return _compat_chase(case)

    @router.get("/chases/{chase_id}/events")
    async def chases_events(chase_id: str, _user: str = Depends(require_session)) -> List[Dict[str, Any]]:
        return await case_events(chase_id, _user)

    @router.post("/chases/run-tick")
    async def chases_run_tick(_user: str = Depends(require_session)) -> Dict[str, Any]:
        result = await run_tick(_user)
        return {"processed": result.get("processed", 0), **result}

    @router.post("/chases/{chase_id}/inject-reply")
    async def chases_inject(
        chase_id: str,
        body: ReplyBody = Body(...),
        _user: str = Depends(require_session),
    ) -> Dict[str, Any]:
        return await inject_reply(chase_id, body, _user)

    @router.post("/chases/{chase_id}/simulate-payment")
    async def chases_pay(chase_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        return await sim_pay(chase_id, _user)

    @router.post("/chases/{chase_id}/pause")
    async def chases_pause(chase_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        return await pause_case(chase_id, _user)

    @router.post("/chases/{chase_id}/resume")
    async def chases_resume(chase_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        return await resume_case(chase_id, _user)

    @router.post("/chases/{chase_id}/close")
    async def chases_close(chase_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        return await close_case(chase_id, _user)

    @router.post("/chases/{chase_id}/restart")
    async def chases_restart(chase_id: str, _user: str = Depends(require_session)) -> Dict[str, Any]:
        return await restart_case(chase_id, _user)

    return router


def _compat_chase(case: Dict[str, Any]) -> Dict[str, Any]:
    """Shape CaseStore row like legacy Chase for old UI during migration."""
    commitments = case.get("commitments") or []
    active = next((c for c in commitments if c.get("status") == "active"), None)
    blockers = case.get("blockers") or []
    open_b = next((b for b in blockers if b.get("status") == "open"), None)
    budget = case.get("budget") or {}
    return {
        **case,
        "id": case.get("id"),
        "promised_date": (active or {}).get("date"),
        "promised_by": (active or {}).get("owner"),
        "missed_count": budget.get("misses_used", 0),
        "nudge_count": budget.get("unanswered_used", 0),
        "postpone_count": budget.get("postponements_used", 0),
        "blocker_type": (open_b or {}).get("type"),
        "blocker_description": (open_b or {}).get("description"),
        "blocker_resolution_date": (open_b or {}).get("expected_resolution"),
    }
