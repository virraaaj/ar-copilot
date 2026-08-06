"""advance clock → process due → run agent tick; demo reset + scenarios."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.outcome_agent.config.seed.day0_2026_07_22 import load_day0_seed
from app.outcome_agent.config.seed.scenarios import get_scenario, list_scenarios
from app.outcome_agent.domain.budgets import consume_miss
from app.outcome_agent.domain.types import AutonomyBudget
from app.outcome_agent.loop.executor import run_case_loop
from app.outcome_agent.loop.post_outcome import judge_due_commitments, reflexion_note
from app.outcome_agent.loop.signal_ingestion import (
    apply_dispute_signal,
    apply_payment_signal,
    apply_reply_signal,
)
from app.outcome_agent.mailbox.store import MailboxStore
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.store.event_ledger import EventLedger
from app.outcome_agent.store.learning_store import LearningStore
from app.services import sim_clock


def _stores(db_path: Optional[str] = None):
    store = CaseStore(db_path=db_path)
    ledger = EventLedger(db_path=store.db_path)
    learning = LearningStore(db_path=store.db_path)
    return store, ledger, learning


async def reset_demo(db_path: Optional[str] = None) -> Dict[str, Any]:
    store, ledger, learning = _stores(db_path)
    await store.wipe_all()
    await ledger.wipe_all()
    await learning.wipe_all()
    from app.outcome_agent.mailbox.store import MailboxStore
    from app.outcome_agent.store.semantic_store import SemanticStore
    from app.outcome_agent.store.trace_store import TraceStore

    await MailboxStore(db_path=store.db_path).wipe_all()
    await TraceStore(db_path=store.db_path).wipe_all()
    await SemanticStore(db_path=store.db_path).wipe_all()
    graph = None
    try:
        from app.services.graph_store import GraphStore

        graph = GraphStore(db_path=store.db_path)
        # Best-effort clear graph tables if present
        import aiosqlite

        async with aiosqlite.connect(store.db_path) as db:
            for table in ("graph_edges", "graph_nodes"):
                try:
                    await db.execute(f"DELETE FROM {table}")
                except Exception:
                    pass
            await db.commit()
    except Exception:
        graph = None
    ids = await load_day0_seed(store, ledger, graph)
    return {
        "ok": True,
        "sim_date": "2026-07-22",
        "cases_loaded": len(ids),
        "case_ids": ids,
    }


async def run_agent_tick(
    settings,
    *,
    db_path: Optional[str] = None,
    case_row_id: Optional[str] = None,
    max_cases: Optional[int] = None,
) -> Dict[str, Any]:
    store, ledger, learning = _stores(db_path)
    now = await sim_clock.now(store.db_path)
    graph = None
    try:
        from app.services.graph_store import GraphStore

        graph = GraphStore(db_path=store.db_path)
    except Exception:
        pass

    # Post-outcome judge on all open promise cases first
    judged = 0
    for case in await store.list_all():
        if case.get("state") in ("promise_to_pay", "promise_missed"):
            results = await judge_due_commitments(case, learning, now=now)
            if results:
                judged += len(results)
                budget = AutonomyBudget.from_dict(case.get("budget") or {})
                if any(r.get("outcome") == "missed" for r in results):
                    consume_miss(budget)
                    case["budget"] = budget.to_dict()
                    if case.get("state") == "promise_to_pay":
                        case["state"] = "promise_missed"
                    case["next_action_at"] = now.isoformat()
                    await ledger.append(
                        case["id"],
                        "promise_missed",
                        {"records": results, "reflexion": reflexion_note(case)},
                        at=now.isoformat(),
                        principles=["P5", "P10", "P13"],
                    )
                await store.update(
                    case["id"],
                    state=case.get("state"),
                    budget=case.get("budget"),
                    commitments=case.get("commitments"),
                    failed_asks=case.get("failed_asks"),
                    next_action_at=case.get("next_action_at"),
                )

    # Promote not_due/due cases to overdue once their due date actually
    # arrives (added 2026-08-06, user request: chase should start
    # automatically when an invoice becomes overdue, not the moment it's
    # synced regardless of due date). Cases created not_due already carry
    # next_action_at = due_date, so this only does real work once that
    # date has passed on the (possibly simulated) clock.
    today = now.date().isoformat()
    for case in await store.list_all():
        if case.get("state") not in ("not_due", "due"):
            continue
        due_date = (case.get("world") or {}).get("due_date")
        if not due_date or due_date > today:
            continue
        case["state"] = "overdue"
        case["next_action_at"] = now.isoformat()
        await ledger.append(
            case["id"],
            "decision",
            {"note": f"due date {due_date} reached -- promoted to overdue"},
            at=now.isoformat(),
            principles=["P12"],
        )
        await store.update(case["id"], state="overdue", next_action_at=now.isoformat())

    if case_row_id:
        due = [await store.get(case_row_id)]
        due = [c for c in due if c]
    else:
        due = await store.list_due(now.isoformat())
        # Also process escalation_required / outreach_ready / customer_responded without next_action
        extras = await store.list_all()
        seen = {c["id"] for c in due}
        for c in extras:
            if c["id"] in seen:
                continue
            if c.get("state") in (
                "outreach_ready",
                "customer_responded",
                "escalation_required",
                "promise_missed",
                "overdue",
            ) and c.get("state") not in ("paused",):
                # only if next_action due or null for responded
                na = c.get("next_action_at")
                if na is None or na <= now.isoformat():
                    due.append(c)

    limit = max_cases or int(
        getattr(settings, "OUTCOME_AGENT_MAX_SENDS_PER_TICK", None)
        or getattr(settings, "CHASE_MAX_SENDS_PER_TICK", 10)
    )
    processed = []
    for case in due[:limit]:
        # refresh
        case = await store.get(case["id"])
        if not case:
            continue
        result = await run_case_loop(
            case,
            store=store,
            ledger=ledger,
            learning=learning,
            trigger="tick",
            now=now,
            settings=settings,
            graph_store=graph,
            mailbox=MailboxStore(db_path=store.db_path),
        )
        processed.append({"case_id": case["id"], "invoice_no": case.get("invoice_no"), **{k: v for k, v in result.items() if k != "case"}})

    return {"processed": len(processed), "judged": judged, "results": processed, "now": now.isoformat()}


async def advance_case_with_reply(
    case_row_id: str,
    reply_text: str,
    settings,
    *,
    db_path: Optional[str] = None,
    run_loop: bool = True,
) -> Dict[str, Any]:
    store, ledger, learning = _stores(db_path)
    case = await store.get(case_row_id)
    if not case:
        raise KeyError(case_row_id)
    now = await sim_clock.now(store.db_path)
    graph = None
    try:
        from app.services.graph_store import GraphStore

        graph = GraphStore(db_path=store.db_path)
    except Exception:
        pass
    # Use the LLM interpreter, not the bare-keyword DEFAULT_INTERPRETER
    # (added 2026-08-04): the deterministic fallback only classifies a
    # blocker if the customer's exact words match a fixed regex list --
    # a real reply like "checking a few things with the team before we
    # make this payment" fell through to UNKNOWN and never became a
    # tracked blocker, which is why the case kept escalating toward
    # firmer tactics instead of routing to resolve_blocker/blocker_ack.
    from app.outcome_agent.adapters.llm_tools import interpret_reply_llm, resolve_llm
    from app.outcome_agent.config.policy_overrides import effective_policy
    from app.outcome_agent.memory.context_builder import build_context_packet

    policy = await effective_policy(case.get("project_number"), settings, db_path=store.db_path)
    client, mock = resolve_llm(settings)
    events = await ledger.list_for_case(case["id"])
    weights = await learning.tactic_weights()
    ctx0 = await build_context_packet(case, events, tactic_weights=weights, graph_store=graph, policy_config=policy)
    fixed_interp, _ = await interpret_reply_llm(client, reply_text, ctx0, mock=mock, now=now)

    class _Fixed:
        def interpret(self, text, *, now=None):
            return fixed_interp

    interp = await apply_reply_signal(case, reply_text, ledger, now=now, interpreter=_Fixed(), graph_store=graph)
    await store.update(
        case["id"],
        state=case.get("state"),
        world=case.get("world"),
        dialogue=case.get("dialogue"),
        budget=case.get("budget"),
        commitments=case.get("commitments"),
        blockers=case.get("blockers"),
        next_action_at=now.isoformat(),
        customer_email=case.get("customer_email"),
        customer_name=case.get("customer_name"),
        target=case.get("target"),
    )
    loop_result = None
    if run_loop and case.get("state") not in ("paid", "disputed", "suppressed"):
        case = await store.get(case["id"])
        loop_result = await run_case_loop(
            case,
            store=store,
            ledger=ledger,
            learning=learning,
            trigger="reply",
            now=now,
            settings=settings,
            graph_store=graph,
            mailbox=MailboxStore(db_path=store.db_path),
        )
    return {"interpretation": interp.__dict__, "loop": loop_result}


async def jump_sim_clock(
    target_date: str,
    settings,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Jump clock, emit date_advanced once, process due, run tick."""
    store, ledger, learning = _stores(db_path)
    # Parse date
    if "T" in target_date:
        when = datetime.fromisoformat(target_date)
    else:
        when = datetime.fromisoformat(target_date + "T09:00:00")
    prev = await sim_clock.now(store.db_path)
    # Idempotency: if already at/after target same day and jump marker exists, still allow but tag
    await sim_clock.set_simulated_at(when, store.db_path)

    # Emit date_advanced on each open case once per jump key
    jump_key = f"date_advanced:{when.date().isoformat()}"
    for case in await store.list_all():
        events = await ledger.list_for_case(case["id"])
        if any((e.get("detail") or {}).get("jump_key") == jump_key for e in events):
            continue
        await ledger.append(
            case["id"],
            "date_advanced",
            {"from": prev.isoformat(), "to": when.isoformat(), "jump_key": jump_key},
            at=when.isoformat(),
            principles=["P12"],
        )
        # Activate follow-ups that are due
        for c in case.get("commitments") or []:
            if c.get("status") == "active" and c.get("type") == "follow_up_date":
                if (c.get("date") or "") <= when.date().isoformat():
                    if case.get("state") == "follow_up_scheduled" or case.get("state") == "blocked":
                        case["state"] = "outreach_ready"
                        case["next_action_at"] = when.isoformat()
        terminal = case.get("state") in (
            "paid",
            "closed",
            "disputed",
            "suppressed",
            "escalated_to_human",
        )
        next_at = case.get("next_action_at")
        if not terminal and not next_at:
            next_at = when.isoformat()
        await store.update(
            case["id"],
            state=case.get("state"),
            next_action_at=None if terminal else next_at,
            commitments=case.get("commitments"),
        )

    tick = await run_agent_tick(settings, db_path=store.db_path)
    return {"now": when.isoformat(), "tick": tick}


async def simulate_payment_for_case(
    case_row_id: str, settings, *, db_path: Optional[str] = None
) -> Dict[str, Any]:
    store, ledger, learning = _stores(db_path)
    case = await store.get(case_row_id)
    if not case:
        raise KeyError(case_row_id)
    now = await sim_clock.now(store.db_path)
    await apply_payment_signal(case, ledger, now=now)
    # credit assignment kept
    dialogue = case.get("dialogue") or {}
    if dialogue.get("last_ask_id"):
        await learning.record(
            ask_id=dialogue["last_ask_id"],
            case_id=case.get("case_id") or "",
            tactic=dialogue.get("last_ask_tactic") or "confirm_promise",
            outcome="kept",
            objective="obtain_commitment",
            scored_at=now.isoformat(),
        )
    await store.update(
        case["id"],
        state="paid",
        world=case["world"],
        commitments=case.get("commitments"),
        next_action_at=None,
    )
    from app.outcome_agent.loop.notifications import send_payment_notice

    await send_payment_notice(case, MailboxStore(db_path=store.db_path), ledger)
    return {"ok": True, "state": "paid"}


async def create_dispute_for_case(
    case_row_id: str, settings, *, note: str = "", db_path: Optional[str] = None
) -> Dict[str, Any]:
    store, ledger, _ = _stores(db_path)
    case = await store.get(case_row_id)
    if not case:
        raise KeyError(case_row_id)
    now = await sim_clock.now(store.db_path)
    await apply_dispute_signal(case, ledger, now=now, note=note)
    await store.update(case["id"], state="disputed", world=case["world"], next_action_at=None)
    return {"ok": True, "state": "disputed"}


async def run_scenario(
    scenario_id: str, settings, *, db_path: Optional[str] = None
) -> Dict[str, Any]:
    store, ledger, learning = _stores(db_path)
    # Always reset first for clean demo
    await reset_demo(store.db_path)
    scenario = get_scenario(scenario_id)
    invoice = scenario["invoice_no"]
    case = await store.get_by_invoice(invoice)
    if not case:
        raise KeyError(f"seed missing invoice {invoice}")

    steps: List[Dict[str, Any]] = []
    for i, beat in enumerate(scenario["beats"]):
        op = beat["op"]
        step: Dict[str, Any] = {
            "step": i + 1,
            "op": op,
            "narration": beat.get("narration", ""),
            "principles": scenario["principles"],
        }
        if op == "inject_reply":
            result = await advance_case_with_reply(
                case["id"], beat["text"], settings, db_path=store.db_path
            )
            step["result"] = {
                "interpretation": result.get("interpretation"),
                "principles_fired": ((result.get("loop") or {}).get("trace") or {}).get("principles_fired"),
            }
        elif op == "jump":
            result = await jump_sim_clock(beat["date"], settings, db_path=store.db_path)
            step["result"] = {"now": result["now"], "processed": result["tick"]["processed"]}
        elif op == "run_tick":
            result = await run_agent_tick(settings, db_path=store.db_path, case_row_id=case["id"])
            step["result"] = result
        elif op == "simulate_payment":
            result = await simulate_payment_for_case(case["id"], settings, db_path=store.db_path)
            step["result"] = result
        elif op == "force_bad_draft":
            case = await store.get(case["id"])
            now = await sim_clock.now(store.db_path)
            graph = None
            try:
                from app.services.graph_store import GraphStore

                graph = GraphStore(db_path=store.db_path)
            except Exception:
                pass
            result = await run_case_loop(
                case,
                store=store,
                ledger=ledger,
                learning=learning,
                trigger="force_bad_draft",
                now=now,
                settings=settings,
                graph_store=graph,
                mailbox=MailboxStore(db_path=store.db_path),
                force_bad_draft=beat.get("text"),
            )
            step["result"] = {
                "critic": (result.get("trace") or {}).get("critic_result"),
                "principles_fired": (result.get("trace") or {}).get("principles_fired"),
            }
        elif op == "assert":
            case = await store.get(case["id"])
            case = await store.get_by_invoice(invoice) or case
            checks = {}
            for name in beat.get("checks") or []:
                checks[name] = _eval_check(name, case, learning, store)
            step["asserts"] = checks
            step["ok"] = all(checks.values())
        steps.append(step)
        # refresh case id (stable)
        case = await store.get_by_invoice(invoice) or case

    final = await store.get_by_invoice(invoice)
    return {
        "scenario_id": scenario_id,
        "title": scenario["title"],
        "principles": scenario["principles"],
        "steps": steps,
        "final_case": final,
    }


def _eval_check(name: str, case: Dict[str, Any], learning, store) -> bool:
    world = case.get("world") or {}
    goals = case.get("goals") or {}
    if name == "world_unpaid":
        return float(world.get("balance_due") or 0) > 0 and world.get("status") != "paid"
    if name == "objective_verify_payment":
        return goals.get("current_objective") == "verify_payment" or (
            (case.get("last_decision") or {}).get("selected_action") or {}
        ).get("objective") == "verify_payment" or (
            (case.get("last_decision") or {}).get("selected_action") or {}
        ).get("tactic") == "verify_payment_ask"
    if name == "escalation_present":
        return bool(case.get("escalation")) or case.get("state") in (
            "escalation_required",
            "escalated_to_human",
        )
    if name == "promise_missed_or_learning":
        return case.get("state") == "promise_missed" or bool(case.get("failed_asks"))
    if name == "reflexion_present":
        return bool(case.get("failed_asks")) or bool(
            (case.get("last_decision") or {}).get("reflexion_note")
        )
    if name == "state_disputed":
        return case.get("state") == "disputed"
    if name == "objective_clarify_or_tactic_clarify":
        sel = (case.get("last_decision") or {}).get("selected_action") or {}
        return (
            goals.get("current_objective") == "clarify_date"
            or sel.get("tactic") == "clarify_ask"
            or sel.get("objective") == "clarify_date"
        )
    if name == "critic_regenerated":
        cr = (case.get("last_decision") or {}).get("critic_result") or {}
        return bool(cr.get("regenerated")) or bool(cr.get("requires_human_review")) or (
            not cr.get("passed", True) if cr else False
        ) or any(
            not c.get("pass")
            for c in (cr.get("checks") or [])
            if c.get("name") == "language_guardrail"
        ) or bool(cr)
    if name == "blocker_closed_commitment_active":
        blockers = case.get("blockers") or []
        commitments = case.get("commitments") or []
        open_blk = [b for b in blockers if b.get("status") == "open"]
        active_pay = [
            c for c in commitments if c.get("status") == "active" and c.get("type") == "payment_date"
        ]
        return len(open_blk) == 0 and len(active_pay) > 0
    return False


# Compat aliases used by cutover
async def process_due_signals(settings, db_path: Optional[str] = None) -> Dict[str, Any]:
    return await run_agent_tick(settings, db_path=db_path)
