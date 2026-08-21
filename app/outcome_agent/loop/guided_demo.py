"""Guided product demo -- three end-to-end scenarios run against the real
agent loop (run_agent_tick / advance_case_with_reply / simulate_payment_for_case),
added 2026-08-06 per user request for a presentable, step-through
demonstration of the agent's actual decision-making -- not a scripted/
canned replay. Each "beat" below is executed by calling the same
functions the live poller and Trace Studio use; the LLM drafts and
classifies for real at every step.

Deliberately separate from config/seed/scenarios.py's S1-S8 packs (those
reset to the day0 seed and are built for automated principle assertions,
not for presenting). This seed creates its own three purpose-built
projects/invoices instead.

"jump" beats deliberately do NOT call scheduler.jump_sim_clock -- that
function sweeps every case in the entire store and runs an unscoped
tick, which is correct for Trace Studio (one scenario at a time) but
wrong here: three scenario cards can run concurrently, and one card's
time jump was firing unscheduled outreach for the OTHER two cards mid-
sequence (found 2026-08-06 during a full transcript review -- a
duplicate send and a step reverting to the first-contact script were
both downstream of this). _scoped_jump below only advances the (still
necessarily global) sim clock and then processes this one case.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from app.outcome_agent.loop.scheduler import (
    _stores,
    advance_case_with_reply,
    run_agent_tick,
    simulate_payment_for_case,
)
from app.services import sim_clock

logger = logging.getLogger(__name__)

SEED_SIM_DATE = datetime(2026, 8, 1, 9, 0, 0)

GUIDED_DEMO_SEED: List[Dict[str, Any]] = [
    {
        "case_id": "demo-case-1001",
        "case_key": "DEMO-INV-1001",
        "invoice_no": "DEMO-INV-1001",
        "project_number": "DEMO-001",
        "customer_name": "Harborview Logistics",
        "amount": 42500.0,
        "due_date": "2026-07-20",
        "pm_email": "demo-pm-harborview@ar-copilot.demo",
    },
    {
        "case_id": "demo-case-1002",
        "case_key": "DEMO-INV-1002",
        "invoice_no": "DEMO-INV-1002",
        "project_number": "DEMO-002",
        "customer_name": "Silverline Manufacturing",
        "amount": 118000.0,
        "due_date": "2026-07-15",
        "pm_email": "demo-pm-silverline@ar-copilot.demo",
    },
    {
        "case_id": "demo-case-1003",
        "case_key": "DEMO-INV-1003",
        "invoice_no": "DEMO-INV-1003",
        "project_number": "DEMO-003",
        "customer_name": "Crestpoint Interiors",
        "amount": 27650.0,
        "due_date": "2026-07-25",
        "pm_email": "demo-pm-crestpoint@ar-copilot.demo",
    },
]

GUIDED_SCENARIOS: List[Dict[str, Any]] = [
    {
        "id": "pm_payment_date_missed_then_paid",
        "title": "Customer commits a date via the PM, misses it, agent recovers and gets paid",
        "invoice_no": "DEMO-INV-1001",
        "project_name": "Harborview Logistics",
        "beats": [
            {"op": "run_tick", "narration": "Agent checks in with the PM to ask if they know a payment date."},
            {"op": "inject_reply", "as": "pm", "text": "I checked with Harborview -- they told me they'll pay by August 20th.", "narration": "PM relays a date from the customer -- the agent tracks it as an active payment promise."},
            # A separate "run_tick" beat used to follow this one, narrated as
            # "the promise is judged missed; the agent follows up." That was
            # always a double-send: run_agent_tick (which _scoped_jump calls
            # internally) judges due payment_date commitments and sends the
            # missed-promise follow-up in the SAME call that advances the
            # clock -- there's nothing left for a following run_tick to do
            # except re-run choose_objective against the now-already-
            # answered state and fire a second, redundant message (observed
            # 2026-08-18 live: a real duplicate outreach using clarify_ask,
            # sent moments after the correct reflexion_reask). Folding the
            # narration into this jump beat (matching how scenario 2's
            # follow-up-date jump already works) removes the extra beat
            # instead of masking the double-send.
            {"op": "jump", "days_after_commitment": 2, "narration": "Clock advances two days past the promised date -- still unpaid. The promise is judged missed and the agent follows up asking for a new date."},
            {"op": "inject_reply", "as": "pm", "text": "Sorry for the delay -- I followed up and Harborview says they can commit to September 1st instead.", "narration": "PM relays a new date after being re-engaged."},
            {"op": "simulate_payment", "narration": "Payment comes in -- invoice cleared."},
        ],
    },
    {
        "id": "pm_redirect_customer_dispute_escalated",
        "title": "PM hands off to the customer, who disputes -- escalated to a human",
        "invoice_no": "DEMO-INV-1002",
        "project_name": "Silverline Manufacturing",
        "beats": [
            {"op": "run_tick", "narration": "Agent checks in with the PM first, as it always does before contacting a customer."},
            # A separate "run_tick" beat used to follow this one, narrated
            # as "the agent reaches out to the customer directly." Same
            # double-send class as scenario 1's jump+run_tick (see that
            # scenario's comment): advance_case_with_reply (called for
            # every inject_reply) sends the loop's next message itself
            # unless the reply parked the case on a genuine future date --
            # this PM reply is a bare handoff with no date, so nothing
            # defers it, and the customer outreach already goes out as
            # part of THIS beat. A following run_tick just re-ran
            # choose_objective against the now-already-answered state and
            # fired a second, redundant message (observed 2026-08-19 live:
            # two back-to-back confirm_promise sends to the customer).
            # Folding the narration in here removes the extra beat instead
            # of masking the double-send.
            {"op": "inject_reply", "as": "pm", "text": "Go ahead and reach out to the customer directly -- their AP contact is billing@silverline-demo.com.", "narration": "PM authorizes contact and hands off to the customer -- the agent reaches out directly."},
            {"op": "inject_reply", "as": "customer", "text": "I'll check on this and get back to you in a week.", "narration": "Customer asks for a week -- the agent tracks that exact follow-up date."},
            {"op": "jump", "days_after_commitment": 0, "narration": "One week later -- the agent follows up exactly as promised."},
            {"op": "inject_reply", "as": "customer", "text": "Actually, we're disputing this invoice -- the amount billed doesn't match what we agreed to.", "narration": "Customer raises a dispute."},
            {"op": "run_tick", "narration": "Agent escalates for human review and emails the PM to let them know."},
        ],
    },
    {
        "id": "pm_then_customer_back_and_forth_paid",
        "title": "PM to customer, a real back-and-forth, then a tracked payment clears",
        "invoice_no": "DEMO-INV-1003",
        "project_name": "Crestpoint Interiors",
        "beats": [
            {"op": "run_tick", "narration": "Agent checks in with the PM first."},
            # PM hands off with no date -> inject_reply's own send already
            # covers "the agent reaches out to the customer" (see scenario
            # 2's identical comment above); the separate run_tick that used
            # to follow was redundant.
            {"op": "inject_reply", "as": "pm", "text": "Please contact the customer directly going forward -- ap@crestpoint-demo.com.", "narration": "PM hands off to the customer -- the agent reaches out directly."},
            # Same double-send class again: this checkback reply has no
            # date, so inject_reply's own send already covers "the agent
            # responds to the customer's question." A following run_tick
            # used to force-reprocess the same case and fire a second,
            # near-identical message before the customer could even see
            # the first one (observed 2026-08-19 live: two back-to-back
            # polite_outreach emails, both re-stating the same balance/due
            # date, sent seconds apart).
            {"op": "inject_reply", "as": "customer", "text": "We're reviewing this internally -- can you confirm the exact balance and due date again?", "narration": "Customer's first reply -- asks the agent to confirm details it already has, not a date yet. The agent responds with those details."},
            # Was a hardcoded "by August 18th" -- fixed 2026-08-20
            # (FIX_PLAN_commitment_grounding.md, Fix 4). This scenario has no
            # "jump" beat before this reply, but the sim clock is a single
            # global value shared by all three guided-demo cases (see module
            # docstring), so if scenario 1 or 2 already ran their own jumps
            # in the same session, the clock could be well past August 18th
            # by the time this beat executes -- the customer would be
            # committing to a date already in the past. A relative phrase
            # ("in 10 days") is computed from whatever "today" the reply
            # interpreter is actually given, so it can never land in the
            # past regardless of what the other two scenarios did to the
            # shared clock first.
            {"op": "inject_reply", "as": "customer", "text": "Got it, thanks. We can commit to paying in 10 days.", "narration": "Customer's second reply -- now commits a date."},
            {"op": "simulate_payment", "narration": "Payment comes in -- invoice cleared."},
        ],
    },
]


# Guards double-resets from racing each other server-side (the frontend
# already disables the reset button while another reset is in flight, but
# this holds regardless of which client is calling in -- a second API
# client, a double-submit that slipped past the UI, etc). Deliberately
# NOT held during run_guided_step: two different scenarios' steps operate
# on independent case rows and are safe to run concurrently -- only the
# full-store wipe here is inherently exclusive with everything else.
_reset_lock = asyncio.Lock()


async def reset_guided_demo(settings, *, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Wipe the outcome-agent store and load exactly the three guided-demo
    cases (PM-first, already overdue so the agent starts working the
    moment the demo runs its first step)."""
    async with _reset_lock:
        return await _do_reset_guided_demo(settings, db_path=db_path)


async def _do_reset_guided_demo(settings, *, db_path: Optional[str] = None) -> Dict[str, Any]:
    store, ledger, learning = _stores(db_path)
    # _stores() (scheduler.py's helper) is a plain-sqlite triple that
    # predates both the graph store and azure-mode support -- it's fine
    # for case/ledger/learning wipe+seed here since CaseStore is always
    # sqlite-backed regardless of mode, but the graph is NOT: this
    # deployment runs OUTCOME_STORE_BACKEND=azure, where the real graph
    # is CosmosGraphAdapter (Postgres+Gremlin), not the sqlite
    # app.services.graph_store.GraphStore. Must go through
    # build_runtime_stores (same call invoice_sync.py makes) so the demo
    # seed writes to whichever backend is actually configured -- hand-
    # instantiating the sqlite GraphStore directly would silently write
    # to a local file nothing in azure mode ever reads from.
    #
    # Without the Customer/Invoice graph nodes a real ingestion writes,
    # Trace Studio's memory.read.graph step finds no neighborhood for
    # demo cases and shows thinner detail than a real invoice (found
    # 2026-08-13 via user report: "details aren't all present there as
    # they would for a regular invoice").
    from app.outcome_agent.runtime.stores import build_runtime_stores

    graph = None
    graph_unavailable = False
    try:
        graph = build_runtime_stores(settings, db_path=store.db_path).graph
    except Exception:
        logger.exception("guided_demo: could not acquire graph store for seeding")
    await store.wipe_all()
    await ledger.wipe_all()
    await learning.wipe_all()
    from app.outcome_agent.mailbox.store import MailboxStore
    from app.outcome_agent.store.semantic_store import SemanticStore
    from app.outcome_agent.store.trace_store import TraceStore

    await MailboxStore(db_path=store.db_path).wipe_all()
    await TraceStore(db_path=store.db_path).wipe_all()
    await SemanticStore(db_path=store.db_path).wipe_all()

    await sim_clock.set_simulated_at(SEED_SIM_DATE, store.db_path)
    today = SEED_SIM_DATE.date().isoformat()
    created: Dict[str, str] = {}
    for spec in GUIDED_DEMO_SEED:
        world = {
            "invoice_no": spec["invoice_no"],
            "case_id": spec["case_id"],
            "balance_due": spec["amount"],
            "status": "open",
            "due_date": spec["due_date"],
            "consent_ok": True,
            "internal_owner": spec["pm_email"],
            "risk": "medium",
            "customer_name": spec["customer_name"],
            "project_number": spec["project_number"],
            "amount_original": spec["amount"],
            "source": "guided_demo",
        }
        row_id = await store.create(
            spec["case_id"],
            case_key=spec["case_key"],
            invoice_no=spec["invoice_no"],
            project_number=spec["project_number"],
            customer_name=spec["customer_name"],
            state="overdue",
            amount=spec["amount"],
            world=world,
            dialogue={},
            budget={
                "max_unanswered": 5,
                "unanswered_used": 0,
                "max_postponements": 5,
                "postponements_used": 0,
                "max_missed_promises": 5,
                "misses_used": 0,
                "min_days_between_emails": 3,
            },
            goals={
                "primary_outcome": "Collect outstanding balance while preserving relationship",
                "current_objective": "establish_contact",
                "selected_tactic": "pm_awareness_check",
                "objective_rationale": "New invoice; no prior contact on file.",
            },
            commitments=[],
            blockers=[],
            pm_email=spec["pm_email"],
            # Real invoice_sync.py resolves customer_email from the same
            # project-contacts lookup as pm_email -- this UAT dataset has
            # no distinct AP contact, so a real case's customer_email
            # equals its pm_email at creation time too (see
            # invoice_sync.py's _resolve_contacts). Was hardcoded None
            # here, which made a not-yet-contacted demo case look thinner
            # than a real one in Trace Studio.
            customer_email=spec["pm_email"],
            target="pm",
        )
        created[spec["invoice_no"]] = row_id
        await ledger.append(
            row_id,
            "seed_loaded",
            {"invoice_no": spec["invoice_no"], "state": "overdue", "demo": True},
            at=SEED_SIM_DATE.isoformat(),
            principles=["P12"],
        )

        if graph is not None and not graph_unavailable:
            try:
                cust_node = f"customer:{spec['project_number']}"
                inv_node = f"invoice:{spec['invoice_no']}"

                async def _write_graph() -> None:
                    await graph.upsert_node(cust_node, "Customer", label=spec["customer_name"])
                    await graph.upsert_node(
                        inv_node, "Invoice", label=spec["invoice_no"], attributes={"balance_due": spec["amount"]}
                    )
                    await graph.supersede_edge(cust_node, "CUSTOMER_HAS_INVOICE", inv_node)

                # Bounded so an unreachable Cosmos/Postgres backend costs
                # the demo a few seconds, not the ~10s-per-call OS-level
                # TCP timeout observed in dev (WinError 121 / semaphore
                # timeout -- a network black hole, not a fast refusal).
                # Graph enrichment is best-effort presentation detail, not
                # correctness-critical (see the try/except this replaces
                # a bare version of); it must never make the whole demo
                # reset noticeably slower than before this fix existed.
                await asyncio.wait_for(_write_graph(), timeout=4.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "guided_demo: graph backend unreachable (timed out) -- skipping graph writes "
                    "for the remaining demo cases this reset"
                )
                graph_unavailable = True
            except Exception:
                logger.exception("guided_demo: graph write failed for %s", spec["invoice_no"])

    return {"ok": True, "sim_date": today, "cases": created}


def list_guided_scenarios() -> List[Dict[str, Any]]:
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "invoice_no": s["invoice_no"],
            "project_name": s["project_name"],
            "steps": [
                {"index": i, "op": b["op"], "as": b.get("as"), "narration": b["narration"]}
                for i, b in enumerate(s["beats"])
            ],
        }
        for s in GUIDED_SCENARIOS
    ]


def resolve_jump_target(
    case: Dict[str, Any], beat: Dict[str, Any], *, scenario_id: str, step_index: int
) -> str:
    """Compute a "jump" beat's target date. Pure/sync and independent of
    the store or sim clock so it's directly unit-testable.

    Relative to this case's own active commitment, not a hardcoded
    absolute date -- the sim clock is a single global value shared by all
    three demo cases (see module docstring), so a beat that hardcoded
    e.g. "jump to 2026-08-08" broke the moment another scenario's own
    jump had already moved the clock past that point (found 2026-08-18:
    running scenario 1 to completion, then scenario 2 without resetting,
    made scenario 2's jump go *backward* and its date math come out
    wrong). Reading the date off this case's own commitment is correct
    regardless of what the other two cases, or a prior run of this same
    scenario, left the clock at. `beat["date"]` is still honored if a
    beat ever needs to force a literal date."""
    target_date = beat.get("date")
    if target_date is not None:
        return target_date
    commitments = case.get("commitments") or []
    active = next(
        (c for c in reversed(commitments) if c.get("status") == "active" and c.get("date")), None
    )
    if not active:
        raise ValueError(
            f"jump beat in {scenario_id} step {step_index} has no explicit date and "
            f"case {case.get('id')} has no active dated commitment to jump relative to"
        )
    days_after = beat.get("days_after_commitment", 0)
    base = date.fromisoformat(active["date"])
    return (base + timedelta(days=days_after)).isoformat()


async def _scoped_jump(
    case_row_id: str, target_date: str, store, ledger, settings
) -> Dict[str, Any]:
    """Advance the (necessarily global) sim clock, then process only THIS
    case -- not scheduler.jump_sim_clock's store-wide sweep. See module
    docstring for why: that sweep fires unscheduled sends for every OTHER
    in-progress case too, which corrupts a concurrently-running scenario's
    sequence."""
    if "T" in target_date:
        when = datetime.fromisoformat(target_date)
    else:
        when = datetime.fromisoformat(target_date + "T09:00:00")
    prev = await sim_clock.now(store.db_path)
    await sim_clock.set_simulated_at(when, store.db_path)

    case = await store.get(case_row_id)
    if not case:
        raise KeyError(case_row_id)

    jump_key = f"date_advanced:{when.date().isoformat()}"
    events = await ledger.list_for_case(case_row_id)
    already_jumped = any((e.get("detail") or {}).get("jump_key") == jump_key for e in events)
    if not already_jumped:
        await ledger.append(
            case_row_id,
            "date_advanced",
            {"from": prev.isoformat(), "to": when.isoformat(), "jump_key": jump_key},
            at=when.isoformat(),
            principles=["P12"],
        )
        for c in case.get("commitments") or []:
            if c.get("status") == "active" and c.get("type") == "follow_up_date":
                if (c.get("date") or "") <= when.date().isoformat():
                    if case.get("state") in ("follow_up_scheduled", "blocked"):
                        case["state"] = "outreach_ready"
                        case["next_action_at"] = when.isoformat()
        terminal = case.get("state") in ("paid", "closed", "disputed", "suppressed", "escalated_to_human")
        next_at = case.get("next_action_at")
        if not terminal and not next_at:
            next_at = when.isoformat()
        await store.update(
            case_row_id,
            state=case.get("state"),
            next_action_at=None if terminal else next_at,
            commitments=case.get("commitments"),
        )

    tick = await run_agent_tick(settings, db_path=store.db_path, case_row_id=case_row_id)
    return {"now": when.isoformat(), "processed": tick.get("processed")}


def _get_scenario(scenario_id: str) -> Dict[str, Any]:
    for s in GUIDED_SCENARIOS:
        if s["id"] == scenario_id:
            return s
    raise KeyError(scenario_id)


async def run_guided_step(
    scenario_id: str, step_index: int, settings, *, db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Execute exactly one beat of one scenario against the real agent
    loop and return a presenter-friendly summary of what happened."""
    scenario = _get_scenario(scenario_id)
    if step_index < 0 or step_index >= len(scenario["beats"]):
        raise IndexError(f"step {step_index} out of range for {scenario_id}")
    beat = scenario["beats"][step_index]

    store, ledger, learning = _stores(db_path)
    case = await store.get_by_invoice(scenario["invoice_no"])
    if not case:
        raise KeyError(f"guided demo not seeded -- missing invoice {scenario['invoice_no']}")

    # Snapshot event ids before the op so the response can return exactly
    # the events THIS step produced (added 2026-08-06, user request: show
    # what the agent actually sent and what the reply actually said, not
    # just a rationale badge) -- a single beat can append more than one
    # ledger event (e.g. run_tick appends both outreach_sent and decision),
    # and only returning the last one was silently dropping the message
    # body half the time.
    events_before_ids = {e["id"] for e in await ledger.list_for_case(case["id"])}

    op = beat["op"]
    result: Dict[str, Any] = {}
    if op == "run_tick":
        tick = await run_agent_tick(settings, db_path=store.db_path, case_row_id=case["id"])
        result = {"processed": tick.get("processed")}
    elif op == "inject_reply":
        r = await advance_case_with_reply(case["id"], beat["text"], settings, db_path=store.db_path)
        result = {
            "interpretation": r.get("interpretation"),
            "loop": (r.get("loop") or {}).get("trace"),
        }
    elif op == "jump":
        target_date = resolve_jump_target(case, beat, scenario_id=scenario_id, step_index=step_index)
        r = await _scoped_jump(case["id"], target_date, store, ledger, settings)
        result = r
    elif op == "simulate_payment":
        r = await simulate_payment_for_case(case["id"], settings, db_path=store.db_path)
        result = r
    else:
        raise ValueError(f"unknown guided-demo op: {op}")

    case_after = await store.get(case["id"])
    events_after = await ledger.list_for_case(case["id"])
    from app.outcome_agent.loop.explain import explain_event

    for e in events_after:
        e["explanation"] = explain_event(case_after, e)
    new_events = [e for e in events_after if e["id"] not in events_before_ids]
    return {
        "scenario_id": scenario_id,
        "step_index": step_index,
        "op": op,
        "narration": beat["narration"],
        "reply_text": beat.get("text") if op == "inject_reply" else None,
        "reply_as": beat.get("as") if op == "inject_reply" else None,
        "result": result,
        "case": case_after,
        "new_events": new_events,
        "latest_events": events_after[-3:],
    }
