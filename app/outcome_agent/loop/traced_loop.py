"""Fully traced invoice follow-up loop — every phase emits a TraceStep."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.outcome_agent.adapters.llm_tools import llm_draft, llm_judge, llm_plan, llm_interpret, resolve_llm
from app.outcome_agent.config.policies import policy_from_settings
from app.outcome_agent.domain.budgets import consume_unanswered, hard_stop_reason
from app.outcome_agent.domain.types import AutonomyBudget, EscalationPack, GoalStack
from app.outcome_agent.loop.guardrails import check_before_send
from app.outcome_agent.loop.next_best_action import plan_next_action
from app.outcome_agent.loop.post_outcome import reflexion_note
from app.outcome_agent.loop.signal_ingestion import apply_reply_signal
from app.outcome_agent.memory.context_builder import build_context_packet
from app.outcome_agent.memory.document import recall_policy
from app.outcome_agent.memory.episodic import recall_episodic
from app.outcome_agent.memory.operational import recall_operational
from app.outcome_agent.memory.semantic import recall_semantic
from app.outcome_agent.memory.temporal_graph import recall_graph
from app.outcome_agent.runtime.stores import annotate_io, build_runtime_stores
from app.outcome_agent.store.case_store import CaseStore
from app.outcome_agent.mailbox.store import MailboxStore
from app.services import sim_clock


async def run_traced_follow_up(
    case_row_id: str,
    *,
    trigger: str = "manual_follow_up",
    settings=None,
    db_path: Optional[str] = None,
    llm: Any = None,
    force_mock_llm: bool = False,
    inbound_text: Optional[str] = None,
    force_bad_draft: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one complete traced cycle for a case. Returns run + case."""
    from app.config import get_settings

    settings = settings or get_settings()
    bundle = build_runtime_stores(settings, db_path=db_path)
    store = bundle.case
    ledger = bundle.ledger
    learning = bundle.learning
    traces = bundle.traces
    mailbox = bundle.mailbox
    facts = bundle.facts
    graph = bundle.graph
    backends = bundle.backends
    policy = policy_from_settings(settings)
    now = await sim_clock.now(store.db_path)

    case = await store.get(case_row_id)
    if not case:
        raise ValueError(f"case not found: {case_row_id}")

    def io(store_name: str, key: str, summary: str, payload: Any = None) -> Dict[str, Any]:
        return annotate_io(backends, store_name, key, summary, payload)

    run_id = await traces.start_run(case["id"], trigger)
    seq = 0
    principles: List[str] = ["P12"]

    async def next_seq() -> int:
        nonlocal seq
        seq += 1
        return seq

    # 1) signal
    s = await next_seq()
    async with traces.step(run_id, s, "signal", f"Run started: {trigger}", principles=["P12"]) as bag:
        bag["reads"].append(io("trigger", trigger, trigger, {"inbound": bool(inbound_text), "store_mode": bundle.mode}))
        bag["reads"].append(io("context", "topology", f"mode={bundle.mode}", bundle.topology()))

    # Optional inbound apply
    if inbound_text:
        s = await next_seq()
        async with traces.step(run_id, s, "mail.receive", "Process inbound customer email", principles=["P1", "P6"]) as bag:
            bag["reads"].append(io("mailbox", "inbound_text", inbound_text[:120], {"text": inbound_text}))
            mock = force_mock_llm
            client = llm
            if client is None and not mock:
                client, mock = resolve_llm(settings)
            events = await ledger.list_for_case(case["id"])
            weights = await learning.tactic_weights()
            ctx0 = await build_context_packet(
                case, events, tactic_weights=weights, graph_store=graph, policy_config=policy
            )
            interp, call = await llm_interpret(client, inbound_text, ctx0, mock=mock or force_mock_llm)
            bag["call"] = call

            # Map LLM taxonomy → domain reply types used by apply_reply_signal
            mapped = dict(interp.__dict__)
            rt = mapped.get("reply_type") or "unknown"
            remap = {
                "payment_promise": "payment_date",
                "follow_up_commitment": "checkback",
                "approval_blocker": "blocker",
                "cash_flow_blocker": "blocker",
                "already_paid": "paid_claim",
                "vague_delay": "vague",
            }
            mapped["reply_type"] = remap.get(rt, rt if rt in (
                "payment_date", "blocker", "checkback", "dispute", "paid_claim", "vague", "unsubscribe", "hostile", "unknown"
            ) else "unknown")
            from app.outcome_agent.loop.reply_interpreter import InterpretedReply as IR

            fixed = IR(**{k: mapped[k] for k in IR.__dataclass_fields__ if k in mapped})

            class _Fixed:
                def interpret(self, text, *, now=None):
                    return fixed

            interp = await apply_reply_signal(
                case, inbound_text, ledger, now=now, interpreter=_Fixed(), graph_store=graph
            )
            bag["writes"].append(io("case", "dialogue+commitments", interp.reply_type, interp.__dict__))
            fact = await facts.upsert(
                case["id"], "reply", "last_reply_type", interp.reply_type, confidence=interp.confidence
            )
            bag["writes"].append(io("semantic", fact["key"], str(fact["value"]), fact))

    # Terminals
    world = case.get("world") or {}
    if float(world.get("balance_due") or 0) <= 0 or case.get("state") == "paid":
        s = await next_seq()
        async with traces.step(run_id, s, "state.transition", "Already paid — skip outreach", principles=["P1"]) as bag:
            bag["status"] = "skipped"
            bag["reads"].append(io("world", "balance_due", str(world.get("balance_due")), world))
        await traces.finish_run(run_id, "ok", "skipped paid")
        return {"run_id": run_id, "skipped": True, "reason": "paid", "case": case}

    if case.get("state") in ("disputed", "suppressed", "closed", "paused", "escalated_to_human"):
        s = await next_seq()
        async with traces.step(run_id, s, "state.transition", f"Terminal/suppressed: {case.get('state')}", principles=["P11"]) as bag:
            bag["status"] = "skipped"
        await traces.finish_run(run_id, "ok", f"skipped {case.get('state')}")
        return {"run_id": run_id, "skipped": True, "reason": case.get("state"), "case": case}

    # Memory reads
    events = await ledger.list_for_case(case["id"])
    weights = await learning.tactic_weights()

    s = await next_seq()
    async with traces.step(run_id, s, "memory.read.operational", "Read operational memory", principles=["P6"]) as bag:
        op = recall_operational(case)
        bag["reads"].append(io("operational", case["id"], f"state={case.get('state')}", op))

    s = await next_seq()
    async with traces.step(run_id, s, "memory.read.episodic", "Read episodic memory", principles=["P6"]) as bag:
        ep = recall_episodic(events)
        bag["reads"].append(io("episodic", "events", f"{len(ep)} recent events", ep))

    s = await next_seq()
    async with traces.step(run_id, s, "memory.read.semantic", "Read semantic facts", principles=["P6"]) as bag:
        sem_facts = await facts.list_active(case["id"])
        bag["reads"].append(io("semantic", "facts", f"{len(sem_facts)} case facts", sem_facts))

    s = await next_seq()
    async with traces.step(run_id, s, "memory.read.learning", "Read learning / tactic weights", principles=["P10"]) as bag:
        failed = case.get("failed_asks") or []
        learning_facts = recall_semantic(failed, weights)
        bag["reads"].append(
            io(
                "learning",
                "tactic_weights",
                f"{len(weights)} tactics, {len(failed)} failed asks",
                {"tactic_weights": weights, "failed_asks": failed[-5:], "as_facts": learning_facts},
            )
        )

    s = await next_seq()
    async with traces.step(run_id, s, "memory.read.document", "Retrieve policy documents", principles=["P6"]) as bag:
        docs = recall_policy("collections follow up contact frequency tone", policy)
        bag["reads"].append(io("document", "policy", f"{len(docs)} snippets", docs))

    s = await next_seq()
    async with traces.step(run_id, s, "memory.read.graph", "Read temporal knowledge graph", principles=["P6"]) as bag:
        gdata = await recall_graph(graph, case.get("invoice_no") or "")
        bag["reads"].append(io("graph", f"invoice:{case.get('invoice_no')}", f"{len(gdata)} edges", gdata,))

    s = await next_seq()
    context: Dict[str, Any] = {}
    async with traces.step(run_id, s, "context.built", "Assemble context packet", principles=["P6", "P7", "P8"]) as bag:
        context = await build_context_packet(
            case, events, tactic_weights=weights, graph_store=graph, policy_config=policy
        )
        context["semantic_facts"] = await facts.list_active(case["id"])
        bag["reads"].append(io("context", "packet", "ContextPacket ready", context))
        principles.extend(context.get("principles_relevant") or [])

    # Budget hard stop
    budget = AutonomyBudget.from_dict(case.get("budget") or {})
    stop = hard_stop_reason(budget)
    if stop or (context.get("goal_stack") or {}).get("current_objective") == "escalate_handoff":
        s = await next_seq()
        async with traces.step(run_id, s, "state.transition", "Escalate — budget/objective", principles=["P3", "P11"]) as bag:
            pack = EscalationPack(
                reason=stop or "Escalation required",
                evidence=[f"state={case.get('state')}", f"budget={budget.to_dict()}"],
                recommended_human_move="Call AP owner; confirm commitment",
                case_id=case.get("case_id") or "",
                invoice_no=case.get("invoice_no") or "",
            )
            case["state"] = "escalated_to_human"
            case["escalation"] = pack.to_dict()
            case["next_action_at"] = None
            bag["writes"].append(io("case", "escalation", pack.reason, pack.to_dict()))
        await store.update(case["id"], state=case["state"], escalation=case["escalation"], next_action_at=None, dialogue=case.get("dialogue"), commitments=case.get("commitments"), blockers=case.get("blockers"))
        await traces.finish_run(run_id, "ok", "escalated")
        return {"run_id": run_id, "escalated": True, "case": case}

    # Plan
    candidates, selected = plan_next_action(context)
    cand_dicts = [c.to_dict() for c in candidates]
    client = llm
    mock = force_mock_llm
    if client is None and not mock:
        client, mock = resolve_llm(settings)

    s = await next_seq()
    plan: Dict[str, Any] = {}
    async with traces.step(run_id, s, "llm.plan", "LLM plan next action", principles=["P8", "P9", "P12"]) as bag:
        plan, call = await llm_plan(client, context, cand_dicts, mock=mock)
        bag["call"] = call
        # Align selected with LLM pick when possible
        for c in candidates:
            if c.tactic == plan.get("selected_tactic"):
                selected = c
                break
        bag["reads"].append(io("candidates", "nba", f"{len(candidates)} candidates", cand_dicts))
        bag["writes"].append(io("plan", "selected", plan.get("selected_tactic"), plan))

    plan_view = {
        "selected_tactic": selected.tactic,
        "objective": selected.objective,
        "rationale": plan.get("rationale") or selected.rationale,
    }

    # Draft + judge (with regen)
    subject = ""
    body = ""
    judgment: Dict[str, Any] = {}
    for attempt in (1, 2):
        s = await next_seq()
        async with traces.step(run_id, s, "llm.draft", f"LLM draft email (attempt {attempt})", principles=["P12", "P4"]) as bag:
            force = force_bad_draft if attempt == 1 and force_bad_draft else None
            subject, body, call = await llm_draft(client, case, plan_view, context, mock=mock, force_text=force)
            bag["call"] = call
            bag["writes"].append(io("draft", "email", subject, {"subject": subject, "body": body}))

        s = await next_seq()
        async with traces.step(run_id, s, "llm.judge", f"LLM/critic judge (attempt {attempt})", principles=["P4", "P12"]) as bag:
            judgment, call = await llm_judge(client, subject, body, case, context, mock=mock)
            bag["call"] = call
            bag["reads"].append(io("draft", "email", "judging draft", {"subject": subject, "body": body}))
            if judgment.get("passed"):
                bag["status"] = "ok"
                break
            bag["status"] = "ok" if attempt == 2 else "ok"
            if attempt == 1 and judgment.get("regenerate", True):
                # clear force bad for regen
                force_bad_draft = None
                continue
            break

    if not judgment.get("passed"):
        s = await next_seq()
        async with traces.step(run_id, s, "guardrails", "Critic failed — human review", principles=["P4"]) as bag:
            bag["status"] = "skipped"
            bag["reads"].append(io("judgment", "failures", str(judgment.get("failures")), judgment))
        await store.add_decision_trace(case["id"], {"run_id": run_id, "blocked": True, "judgment": judgment})
        await traces.finish_run(run_id, "ok", "blocked by critic")
        return {"run_id": run_id, "blocked": True, "judgment": judgment, "case": case}

    # Guardrails
    s = await next_seq()
    guard = None
    async with traces.step(run_id, s, "guardrails", "Policy guardrails before send", principles=["P4"]) as bag:
        guard = check_before_send(
            case,
            body,
            now=now,
            dry_run=policy.dry_run,
            allowlist=policy.to_address_allowlist,
            recipient=case.get("customer_email"),
        )
        bag["reads"].append(io("policy", "guard", "allowed" if guard.allowed else guard.reason, {"allowed": guard.allowed, "checked": guard.policies_checked}))
        if not guard.allowed:
            bag["status"] = "skipped"
    if guard and not guard.allowed:
        await traces.finish_run(run_id, "ok", f"guard blocked: {guard.reason}")
        return {"run_id": run_id, "blocked": True, "reason": guard.reason, "case": case}

    # Send mail
    state_before = case.get("state")
    ask_id = f"ask-{uuid4().hex[:8]}"
    recipient = case.get("customer_email") or "customer@example.com"
    from_addr = "ar-agent@local.mailbox"
    thread_id = case.get("subject_token") or case["id"]

    s = await next_seq()
    msg: Dict[str, Any] = {}
    async with traces.step(run_id, s, "mail.send", "Send email to local mailbox", principles=["P2", "P12"]) as bag:
        msg = await mailbox.send(
            case_id=case["id"],
            thread_id=thread_id,
            to_addr=recipient,
            from_addr=from_addr,
            subject=subject,
            body=body,
            headers={"X-Ask-Id": ask_id, "X-Tactic": selected.tactic},
        )
        await store.add_outbox(
            case["id"],
            body,
            channel="email",
            recipient=recipient,
            subject=subject,
            dry_run=policy.dry_run,
            meta={"ask_id": ask_id, "mailbox_id": msg["id"], "tactic": selected.tactic},
        )
        bag["writes"].append(io("mailbox", msg["id"], f"to {recipient}", msg))
        await ledger.append(
            case["id"],
            "outreach_sent",
            {"body": body, "subject": subject, "recipient": recipient, "mailbox_id": msg["id"], "ask_id": ask_id},
            at=now.isoformat(),
            principles=["P4", "P12"],
        )

    # Memory / graph writes
    s = await next_seq()
    async with traces.step(run_id, s, "memory.write", "Write operational + semantic memory", principles=["P6", "P13"]) as bag:
        consume_unanswered(budget)
        case["budget"] = budget.to_dict()
        dialogue = dict(case.get("dialogue") or {})
        dialogue["latest_outbound"] = body
        dialogue["last_ask_id"] = ask_id
        dialogue["last_ask_tactic"] = selected.tactic
        case["dialogue"] = dialogue
        case["last_outreach_at"] = now.isoformat()
        if case.get("state") in ("customer_responded", "promise_missed", "outreach_ready", "overdue", "due", "blocked"):
            if case.get("state") != "promise_to_pay":
                case["state"] = "waiting_for_customer"
        case["next_action_at"] = (now + timedelta(days=policy.nudge_interval_days)).isoformat()
        goals = GoalStack.from_dict(context.get("goal_stack") or {})
        goals.selected_tactic = selected.tactic
        goals.current_objective = selected.objective
        case["goals"] = goals.to_dict()
        note = reflexion_note(case)
        fact = await facts.upsert(case["id"], "outreach", "last_ask_tactic", selected.tactic, confidence=1.0)
        bag["writes"].append(io("semantic", fact["key"], selected.tactic, fact))
        bag["writes"].append(
            io(
                "case",
                "state",
                f"{state_before} -> {case.get('state')}",
                {"before": state_before, "after": case.get("state"), "reflexion": note},
            )
        )

    s = await next_seq()
    async with traces.step(run_id, s, "graph.write", "Update temporal graph", principles=["P6"]) as bag:
        if graph and case.get("invoice_no"):
            inv = f"invoice:{case.get('invoice_no')}"
            await graph.upsert_node(inv, "Invoice", label=case.get("invoice_no"))
            for cmt in case.get("commitments") or []:
                if cmt.get("status") == "active" and cmt.get("date"):
                    cid = f"commitment:{cmt.get('id')}"
                    await graph.upsert_node(cid, "Commitment", label=cmt.get("date"))
                    await graph.supersede_edge(inv, "INVOICE_HAS_COMMITMENT", cid)
                    bag["writes"].append(io("graph", "INVOICE_HAS_COMMITMENT", cmt.get("date"), cmt))
            for blk in case.get("blockers") or []:
                if blk.get("status") == "open":
                    bid = f"blocker:{blk.get('id')}"
                    await graph.upsert_node(bid, "Blocker", label=blk.get("type"))
                    await graph.supersede_edge(inv, "INVOICE_BLOCKED_BY", bid)
                    bag["writes"].append(io("graph", "INVOICE_BLOCKED_BY", blk.get("type"), blk))
        else:
            bag["status"] = "skipped"

    s = await next_seq()
    async with traces.step(run_id, s, "state.transition", "Persist state transition", principles=["P2", "P8"]) as bag:
        await store.update(
            case["id"],
            state=case.get("state"),
            world=case.get("world"),
            dialogue=case.get("dialogue"),
            budget=case.get("budget"),
            goals=case.get("goals"),
            commitments=case.get("commitments"),
            blockers=case.get("blockers"),
            failed_asks=case.get("failed_asks"),
            escalation=case.get("escalation"),
            next_action_at=case.get("next_action_at"),
            last_outreach_at=case.get("last_outreach_at"),
            last_decision={"run_id": run_id, "tactic": selected.tactic, "objective": selected.objective},
        )
        bag["writes"].append(io("case", case["id"], f"{state_before} → {case.get('state')}", {"before": state_before, "after": case.get("state"), "next_action_at": case.get("next_action_at")},))

    s = await next_seq()
    async with traces.step(run_id, s, "schedule", "Schedule next wake-up", principles=["P2", "P3"]) as bag:
        bag["writes"].append(io("scheduler", "next_action_at", case.get("next_action_at") or "none", {"next_action_at": case.get("next_action_at"), "nudge_days": policy.nudge_interval_days},))

    await store.add_decision_trace(
        case["id"],
        {
            "run_id": run_id,
            "trigger": trigger,
            "selected_action": selected.to_dict(),
            "plan": plan_view,
            "judgment": judgment,
            "mailbox_id": msg.get("id"),
            "principles_fired": sorted(set(principles + ["P4", "P6", "P8", "P9", "P12"])),
        },
    )
    await traces.finish_run(run_id, "ok", f"sent {msg.get('id')} tactic={selected.tactic}")
    refreshed = await store.get(case["id"])
    return {
        "run_id": run_id,
        "case": refreshed,
        "mailbox_message": msg,
        "subject": subject,
        "body": body,
        "plan": plan_view,
        "judgment": judgment,
        "store_topology": bundle.topology(),
    }


async def submit_customer_reply(
    case_row_id: str,
    text: str,
    *,
    settings=None,
    db_path: Optional[str] = None,
    llm: Any = None,
    force_mock_llm: bool = False,
) -> Dict[str, Any]:
    """Mailbox inbound + traced follow-up."""
    from app.config import get_settings

    settings = settings or get_settings()
    store = CaseStore(db_path=db_path)
    mailbox = MailboxStore(db_path=store.db_path)
    case = await store.get(case_row_id)
    if not case:
        raise ValueError("case not found")
    thread_id = case.get("subject_token") or case["id"]
    prior = await mailbox.list_for_case(case["id"])
    in_reply_to = None
    for m in reversed(prior):
        if m["direction"] == "outbound":
            in_reply_to = (m.get("headers") or {}).get("Message-Id")
            break
    inbound = await mailbox.receive_reply(
        case_id=case["id"],
        thread_id=thread_id,
        from_addr=case.get("customer_email") or "customer@example.com",
        to_addr="ar-agent@local.mailbox",
        subject=f"Re: [{thread_id}] Invoice {case.get('invoice_no')}",
        body=text,
        in_reply_to=in_reply_to,
    )
    result = await run_traced_follow_up(
        case_row_id,
        trigger="customer_reply",
        settings=settings,
        db_path=db_path,
        llm=llm,
        force_mock_llm=force_mock_llm,
        inbound_text=text,
    )
    result["inbound_message"] = inbound
    return result
