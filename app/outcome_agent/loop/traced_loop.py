"""Fully traced invoice follow-up loop — every phase emits a TraceStep."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.outcome_agent.adapters.llm_tools import llm_draft, llm_judge, llm_plan, interpret_reply_llm, resolve_llm
from app.outcome_agent.domain.budgets import consume_unanswered, hard_stop_reason
from app.outcome_agent.domain.types import AutonomyBudget, EscalationPack, GoalStack
from app.outcome_agent.loop.communication import DEFAULT_GENERATOR, PM_DIRECTED_TACTICS, resolve_recipient
from app.outcome_agent.loop.concurrency_guard import get_case_lock, guarded_case_update
from app.outcome_agent.loop.disclosure import append_disclosure, has_disclosure, is_customer_bound
from app.outcome_agent.loop.guardrails import AgentGuardrailResult, check_before_send
from app.outcome_agent.loop.next_best_action import plan_next_action
from app.outcome_agent.loop.notifications import send_escalation_notice, send_payment_notice
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
    now = await sim_clock.now(store.db_path)

    case = await store.get(case_row_id)
    if not case:
        raise ValueError(f"case not found: {case_row_id}")

    from app.outcome_agent.config.policy_overrides import effective_policy

    policy = await effective_policy(case.get("project_number"), settings, db_path=store.db_path)

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
            fixed, call = await interpret_reply_llm(client, inbound_text, ctx0, mock=mock or force_mock_llm, now=now)
            bag["call"] = call

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
    state_before_terminal_check = case.get("state")
    if float(world.get("balance_due") or 0) <= 0 or case.get("state") == "paid":
        s = await next_seq()
        async with traces.step(run_id, s, "state.transition", "Already paid — skip outreach", principles=["P1"]) as bag:
            bag["status"] = "skipped"
            bag["reads"].append(io("world", "balance_due", str(world.get("balance_due")), world))
        newly_paid = state_before_terminal_check != "paid"
        if newly_paid:
            case["state"] = "paid"
            await ledger.append(case["id"], "payment_posted", {"paid_at": world.get("paid_at")})
            await store.update(case["id"], state="paid", next_action_at=None)
            await send_payment_notice(case, mailbox, ledger)
        await traces.finish_run(run_id, "ok", "skipped paid")
        return {"run_id": run_id, "skipped": True, "reason": "paid", "case": case}

    # "disputed" deliberately excluded -- see executor.py's matching
    # comment; it must run through the normal loop to actually escalate.
    if case.get("state") in ("suppressed", "closed", "paused", "escalated_to_human"):
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
        # Terminal-state guard (added 2026-09-04): a concurrent AR-aging
        # upload can have paid-and-closed this case in the DB during the
        # LLM plan/draft calls above -- guarded_case_update() re-reads the
        # DB first and drops state/world/next_action_at from this write if
        # so, instead of blindly overwriting the DB's closure with this
        # escalation branch's stale-relative-to-now state.
        await guarded_case_update(store, case, state=case["state"], escalation=case["escalation"], next_action_at=None, dialogue=case.get("dialogue"), commitments=case.get("commitments"), blockers=case.get("blockers"), target=case.get("target"), customer_email=case.get("customer_email"), customer_name=case.get("customer_name"))
        await send_escalation_notice(case, mailbox, ledger)
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

    # Escalate-kind selection (added 2026-09-03): action_simulator.py sets
    # kind="escalate" for the escalation_pack tactic -- a real, non-hypothetical
    # planner outcome (see plan_next_action / CandidateAction). executor.py
    # already special-cases this (its `wants_send` check + the
    # `elif selected.kind == "escalate" or selected.tactic == "escalation_pack"`
    # branch), but this traced path had no matching branch: it fell straight
    # through to draft -> judge -> send and emailed the resolved recipient --
    # which for a non-PM-directed tactic is the CUSTOMER -- at the exact
    # moment the agent decided it must stop and hand off to a human. Mirrors
    # executor.py's handling and this function's own "recipient is None ->
    # escalate" branch below: build the EscalationPack, mark the case
    # escalated, notify, and return without ever drafting/sending outreach.
    if selected.kind == "escalate" or selected.tactic == "escalation_pack":
        s = await next_seq()
        async with traces.step(run_id, s, "state.transition", "Escalate — planner selected escalation handoff", principles=["P3", "P11"]) as bag:
            pack = EscalationPack(
                reason="Planner selected escalation handoff",
                evidence=[f"tactic={selected.tactic}", f"score={selected.score}"],
                recommended_human_move="Owner call + payment plan options",
                case_id=case.get("case_id") or "",
                invoice_no=case.get("invoice_no") or "",
            )
            case["state"] = "escalated_to_human"
            case["escalation"] = pack.to_dict()
            case["next_action_at"] = None
            bag["writes"].append(io("case", "escalation", pack.reason, pack.to_dict()))
        # Terminal-state guard (added 2026-09-04): a concurrent AR-aging
        # upload can have paid-and-closed this case in the DB during the
        # LLM plan/draft calls above -- guarded_case_update() re-reads the
        # DB first and drops state/world/next_action_at from this write if
        # so, instead of blindly overwriting the DB's closure with this
        # escalation branch's stale-relative-to-now state.
        await guarded_case_update(store, case, state=case["state"], escalation=case["escalation"], next_action_at=None, dialogue=case.get("dialogue"), commitments=case.get("commitments"), blockers=case.get("blockers"), target=case.get("target"), customer_email=case.get("customer_email"), customer_name=case.get("customer_name"))
        await send_escalation_notice(case, mailbox, ledger)
        await traces.finish_run(run_id, "ok", "escalated: planner selected escalation handoff")
        return {"run_id": run_id, "escalated": True, "case": case}

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

    used_safe_fallback = False
    original_judgment = judgment
    if not judgment.get("passed"):
        # Both attempts failed the critic. Fall back to the deterministic,
        # safe-by-construction template for this tactic rather than
        # dropping the send entirely -- see executor.py's matching fix
        # (2026-08-18) for the full rationale: this used to return here
        # with nothing sent, no Outbox entry, and no way for a human to
        # even see a draft was blocked.
        s = await next_seq()
        async with traces.step(run_id, s, "guardrails", "Critic failed twice — sending safe fallback", principles=["P4"]) as bag:
            bag["status"] = "ok"
            bag["reads"].append(io("judgment", "failures", str(judgment.get("failures")), judgment))
            body = DEFAULT_GENERATOR.generate(case, selected.tactic, selected.objective)
            subject = f"[{case.get('subject_token')}] Invoice {case.get('invoice_no')}"
            bag["writes"].append(io("draft", "fallback_email", subject, {"subject": subject, "body": body}))
        await ledger.append(
            case["id"], "critic_blocked", {"checks": judgment.get("failures"), "notes": judgment.get("notes")},
            at=now.isoformat(), principles=["P4"],
        )
        used_safe_fallback = True
        judgment = {
            "passed": True,
            "failures": [],
            "regenerate": False,
            "notes": "Safe template fallback after critic block on the AI draft — flagged for human review.",
        }

    # Note: this function used to return early here with the customer's
    # interpreted reply (blocker/commitment/state/dialogue) never
    # persisted -- found 2026-08-05 via mass-conversation stress testing,
    # since the whole run stopped the moment the agent's own ack email
    # failed the critic twice. Now that a blocked draft falls through to
    # the safe-template send below instead of returning, that persist
    # happens naturally as part of the normal send path -- nothing extra
    # needed here.

    # Was unconditionally case["customer_email"] -- meant no tactic could
    # ever actually reach the PM even if one existed. Fixed 2026-08-06
    # alongside adding pm_awareness_check. Resolved before the guardrail
    # check (not just before sending) so the allowlist check below
    # actually validates the address this message is really going to,
    # not always customer_email regardless of target -- found 2026-08-18
    # via user review.
    recipient = resolve_recipient(case, selected.tactic)
    if recipient is None:
        # The address for the party this tactic is meant to reach (PM or
        # customer, per case.target) isn't actually known. Escalate
        # instead of falling back to a placeholder or the OTHER party's
        # address -- never guess who to email.
        s = await next_seq()
        who = "PM" if (case.get("target") == "pm" or selected.tactic in PM_DIRECTED_TACTICS) else "customer"
        async with traces.step(run_id, s, "state.transition", f"Escalate — no known {who} email", principles=["P3", "P11"]) as bag:
            pack = EscalationPack(
                reason=f"No known {who} email on file — cannot send tactic={selected.tactic}",
                evidence=[f"tactic={selected.tactic}", f"target={case.get('target')}"],
                recommended_human_move=f"Add a {who} contact email to this case, then resume",
                case_id=case.get("case_id") or "",
                invoice_no=case.get("invoice_no") or "",
            )
            case["state"] = "escalated_to_human"
            case["escalation"] = pack.to_dict()
            case["next_action_at"] = None
            bag["writes"].append(io("case", "escalation", pack.reason, pack.to_dict()))
        # Terminal-state guard (added 2026-09-04): a concurrent AR-aging
        # upload can have paid-and-closed this case in the DB during the
        # LLM plan/draft calls above -- guarded_case_update() re-reads the
        # DB first and drops state/world/next_action_at from this write if
        # so, instead of blindly overwriting the DB's closure with this
        # escalation branch's stale-relative-to-now state.
        await guarded_case_update(store, case, state=case["state"], escalation=case["escalation"], next_action_at=None, dialogue=case.get("dialogue"), commitments=case.get("commitments"), blockers=case.get("blockers"), target=case.get("target"), customer_email=case.get("customer_email"), customer_name=case.get("customer_name"))
        await send_escalation_notice(case, mailbox, ledger)
        await traces.finish_run(run_id, "ok", "escalated: no known recipient")
        return {"run_id": run_id, "escalated": True, "case": case}

    # Customer disclosure (added 2026-09-03): appended here, deterministically
    # in code, BEFORE the guardrail/language check below so the disclosure
    # text itself is covered by check_message_language and every copy of
    # this draft (mailbox, outbox, real send, ledger) is identical -- see
    # executor.py's matching fix (same date) for the full rationale. PM-
    # bound sends are untouched. append_disclosure() is idempotent.
    if is_customer_bound(case, selected.tactic):
        body = append_disclosure(body)

    # Hold the per-case lock across guard -> send -> persist only
    # (added 2026-09-04): mirrors executor.py's matching lock -- shared
    # registry (concurrency_guard.py) so the tick/poller path and this
    # manual-follow-up/reply path serialize against EACH OTHER on the
    # same case. Deliberately does NOT wrap the signal/memory reads or
    # plan/draft/judge LLM calls above -- those must never block behind
    # another in-flight send for this case.
    lock = get_case_lock(case["id"])
    async with lock:
        # Guardrails
        s = await next_seq()
        guard = None
        async with traces.step(run_id, s, "guardrails", "Policy guardrails before send", principles=["P4"]) as bag:
            # Re-read the case immediately before the guard (added 2026-09-04):
            # by this point the run has awaited through signal ingestion,
            # several memory reads, and the plan/draft/judge LLM calls --
            # plenty of yield points for a concurrent AR-aging upload to have
            # marked this case paid and closed. check_before_send() must see
            # that current DB state, not the stale `case` this function has
            # been carrying since its own fetch at the top. recipient/
            # allowlist are unaffected -- resolved above from case identity/
            # tactic, which don't go stale this way.
            fresh = await store.get(case["id"])
            if fresh is None:
                # Row vanished entirely -- treat like a failed guard: no send.
                guard = AgentGuardrailResult(False, "case no longer exists in store", ["existence_check"])
            else:
                guard = check_before_send(
                    fresh,
                    body,
                    now=now,
                    allowlist=policy.to_address_allowlist,
                    recipient=recipient,
                )
            bag["reads"].append(io("policy", "guard", "allowed" if guard.allowed else guard.reason, {"allowed": guard.allowed, "checked": guard.policies_checked}))
            if not guard.allowed:
                bag["status"] = "skipped"
        if guard and not guard.allowed:
            await traces.finish_run(run_id, "ok", f"guard blocked: {guard.reason}")
            return {"run_id": run_id, "blocked": True, "reason": guard.reason, "case": case}

        # Fail-closed disclosure gate (added 2026-09-03): the disclosure is
        # already appended above for every customer-bound draft, but this is
        # the last check before the actual send below -- if some future change
        # to this function ever drops the disclosure off a customer-bound body,
        # we must not silently send it as if a person wrote it. Mirrors the
        # matching gate in executor.py (same date).
        if is_customer_bound(case, selected.tactic) and not has_disclosure(body):
            await traces.finish_run(run_id, "ok", "blocked: customer-bound draft missing disclosure")
            return {
                "run_id": run_id,
                "blocked": True,
                "reason": "customer-bound draft missing disclosure",
                "case": case,
            }

        # Send mail
        state_before = case.get("state")
        ask_id = f"ask-{uuid4().hex[:8]}"
        from_addr = "ar-agent@local.mailbox"
        thread_id = case.get("subject_token") or case["id"]

        s = await next_seq()
        msg: Dict[str, Any] = {}
        real_send_result: Optional[Dict[str, Any]] = None
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
            outbox_meta: Dict[str, Any] = {"ask_id": ask_id, "mailbox_id": msg["id"], "tactic": selected.tactic}
            if used_safe_fallback:
                outbox_meta["human_review"] = True
                outbox_meta["fallback_reason"] = original_judgment.get("notes") or "; ".join(original_judgment.get("failures") or [])
            await store.add_outbox(
                case["id"],
                body,
                channel="email",
                recipient=recipient,
                subject=subject,
                meta=outbox_meta,
            )
            bag["writes"].append(io("mailbox", msg["id"], f"to {recipient}", msg))
            # Real send: mirrors master's chase_engine.py wiring --
            # get_email_sender() returns GraphEmailSender the moment
            # GRAPH_MAIL_* + EMAIL_FROM_ADDRESS are set.
            from app.services.email_sender import get_email_sender

            try:
                real_send_result = await get_email_sender().send(recipient, subject, f"<p>{body}</p>", [])
                bag["writes"].append(io("email", recipient, real_send_result.get("provider", "email"), real_send_result))
            except Exception as exc:  # noqa: BLE001
                real_send_result = {"success": False, "error": str(exc)}
                bag["writes"].append(io("email", recipient, "real send failed", real_send_result))
            await ledger.append(
                case["id"],
                "outreach_sent",
                {
                    "body": body,
                    "subject": subject,
                    "recipient": recipient,
                    "mailbox_id": msg["id"],
                    "ask_id": ask_id,
                    "real_send": real_send_result,
                    **({"human_review": True, "fallback_reason": outbox_meta["fallback_reason"]} if used_safe_fallback else {}),
                },
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
            # "blocked" deliberately excluded (found via mass conversation
            # testing, 2026-08-04): this used to reset a just-classified
            # blocker straight back to waiting_for_customer the moment the
            # blocker_ack email was sent, so the blocker only "existed" for
            # the duration of this one function call -- by the next turn it
            # was gone, and the no-firm-tactics-while-blocked protection no
            # longer applied. A blocker should stay open until the customer
            # actually resolves it (apply_reply_signal's payment_date branch
            # already closes it correctly when that happens).
            if case.get("state") in ("customer_responded", "promise_missed", "outreach_ready", "overdue", "due"):
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
            # Terminal-state guard (added 2026-09-04): see guarded_case_update()
            # docstring -- refuses to let this stale-relative-to-now `case`
            # overwrite a paid/closed/escalated state a concurrent AR-aging
            # upload already wrote to the DB while this run's earlier awaits
            # (LLM calls, mail.send) were in flight.
            await guarded_case_update(
                store,
                case,
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
                customer_email=case.get("customer_email"),
                customer_name=case.get("customer_name"),
                target=case.get("target"),
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
