"""Action executor — outbox, schedule, escalate, state updates."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.outcome_agent.adapters.llm_tools import llm_draft, llm_judge, llm_plan, resolve_llm
from app.outcome_agent.domain.budgets import consume_unanswered, hard_stop_reason
from app.outcome_agent.domain.types import AutonomyBudget, EscalationPack, GoalStack
from app.outcome_agent.loop.communication import resolve_recipient
from app.outcome_agent.loop.guardrails import check_before_send
from app.outcome_agent.loop.next_best_action import plan_next_action
from app.outcome_agent.loop.notifications import send_escalation_notice, send_payment_notice
from app.outcome_agent.loop.post_outcome import reflexion_note
from app.outcome_agent.memory.context_builder import build_context_packet


async def run_case_loop(
    case: Dict[str, Any],
    *,
    store,
    ledger,
    learning,
    trigger: str,
    now: datetime,
    settings,
    graph_store=None,
    force_bad_draft: Optional[str] = None,
    mailbox=None,
) -> Dict[str, Any]:
    """Full observe→plan→simulate→critic→guard→execute for one case."""
    from app.outcome_agent.config.policy_overrides import effective_policy

    policy = await effective_policy(case.get("project_number"), settings, db_path=store.db_path)
    state_before = case.get("state")
    principles: List[str] = ["P12", "P8", "P9", "P4"]

    # Hard terminals — no outreach
    world = case.get("world") or {}
    if float(world.get("balance_due") or 0) <= 0 or world.get("status") == "paid" or state_before == "paid":
        newly_paid = state_before != "paid"
        case["state"] = "paid"
        await store.update(case["id"], state="paid", next_action_at=None, world=case["world"])
        if newly_paid:
            await ledger.append(case["id"], "payment_posted", {"paid_at": world.get("paid_at")})
            await send_payment_notice(case, mailbox, ledger)
        return {"skipped": True, "reason": "paid"}

    if state_before in ("disputed", "suppressed", "closed", "paused", "escalated_to_human"):
        return {"skipped": True, "reason": state_before}

    events = await ledger.list_for_case(case["id"])
    weights = await learning.tactic_weights()
    context = await build_context_packet(
        case, events, tactic_weights=weights, graph_store=graph_store, policy_config=policy
    )
    principles.extend(context.get("principles_relevant") or [])

    # Budget hard stop before planning sends
    budget = AutonomyBudget.from_dict(case.get("budget") or {})
    stop = hard_stop_reason(budget)
    if stop or context.get("goal_stack", {}).get("current_objective") == "escalate_handoff":
        pack = EscalationPack(
            reason=stop or "Escalation required",
            evidence=[
                f"state={case.get('state')}",
                f"unanswered={budget.unanswered_used}/{budget.max_unanswered}",
                f"misses={budget.misses_used}/{budget.max_missed_promises}",
            ],
            recommended_human_move="Call AP owner; confirm commitment or write-off path",
            case_id=case.get("case_id") or "",
            invoice_no=case.get("invoice_no") or "",
        )
        case["state"] = "escalated_to_human"
        case["escalation"] = pack.to_dict()
        case["next_action_at"] = None
        principles.extend(["P3", "P11"])
        trace = _trace(
            case, trigger, state_before, "escalated_to_human", [], None, None,
            principles, f"Escalation dossier: {pack.reason}", context, now,
        )
        await _persist(store, ledger, case, trace, principles)
        await send_escalation_notice(case, mailbox, ledger)
        return {"trace": trace, "escalated": True}

    candidates, selected = plan_next_action(context)
    cand_dicts = [c.to_dict() for c in candidates]

    # LLM-based plan/draft/judge (added 2026-08-04): this used to be a
    # fixed template (TemplateCommunicationGenerator) with a deterministic
    # regex critic, which is what the automatic poller and inject-reply
    # both ran on -- it can't read the conversation, can't act on
    # anything the customer actually said, and produced generic
    # one-liners with no context. Now matches traced_loop.py's
    # plan/draft/judge exactly, so every trigger type (manual follow-up,
    # poller tick, inject-reply) gets the same quality of response.
    client, mock = resolve_llm(settings)
    plan_view = {"selected_tactic": selected.tactic, "objective": selected.objective, "rationale": selected.rationale}
    if not force_bad_draft:
        plan, _ = await llm_plan(client, context, cand_dicts, mock=mock)
        for c in candidates:
            if c.tactic == plan.get("selected_tactic"):
                selected = c
                break
        plan_view = {
            "selected_tactic": selected.tactic,
            "objective": selected.objective,
            "rationale": plan.get("rationale") or selected.rationale,
        }
    selected_d = selected.to_dict()

    subject = f"[{case.get('subject_token')}] Invoice {case.get('invoice_no')}"
    final_draft = ""
    judgment: Dict[str, Any] = {}
    regenerated = False
    for attempt in (1, 2):
        force = force_bad_draft if attempt == 1 and force_bad_draft else None
        subject, final_draft, _ = await llm_draft(client, case, plan_view, context, mock=mock, force_text=force)
        judgment, _ = await llm_judge(client, subject, final_draft, case, context, mock=mock)
        if judgment.get("passed"):
            break
        if attempt == 1 and judgment.get("regenerate", True):
            force_bad_draft = None
            regenerated = True
            continue
        break
    selected_d["draft_text"] = final_draft

    if not judgment.get("passed"):
        principles.append("P4")
        trace = _trace(
            case, trigger, state_before, state_before, cand_dicts, selected_d,
            judgment, principles,
            "Critic blocked send — human review", context, now,
            reflexion=reflexion_note(case),
        )
        await store.add_decision_trace(case["id"], trace)
        await ledger.append(case["id"], "critic_blocked", {"checks": judgment.get("failures")}, at=now.isoformat(), principles=["P4"])
        return {"trace": trace, "blocked": True}

    guard = check_before_send(
        case,
        final_draft,
        now=now,
        allowlist=policy.to_address_allowlist,
        recipient=case.get("customer_email"),
    )
    if not guard.allowed:
        trace = _trace(
            case, trigger, state_before, state_before, cand_dicts, selected_d,
            judgment, principles + ["P4"],
            f"Guardrail blocked: {guard.reason}", context, now,
            policies=guard.policies_checked,
        )
        await store.add_decision_trace(case["id"], trace)
        return {"trace": trace, "blocked": True, "reason": guard.reason}

    # Execute
    tools = []
    ask_id = f"ask-{uuid4().hex[:8]}"
    if selected.kind == "escalate" or selected.tactic == "escalation_pack":
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
        tools.append("escalation_pack")
        principles.extend(["P11", "P3"])
        await send_escalation_notice(case, mailbox, ledger)
    else:
        recipient = resolve_recipient(case, selected.tactic)
        await store.add_outbox(
            case["id"],
            final_draft,
            channel="email",
            recipient=recipient,
            subject=subject,
            meta={"tactic": selected.tactic, "ask_id": ask_id},
        )
        tools.append("outbox")
        # Mirror into the shared Mailbox (added 2026-08-03): run_case_loop
        # (the tick/poller path) previously only wrote to the case's own
        # oa_outbox, so anything the poller sent never appeared on the
        # Mailbox tab -- only outreach sent via the traced run-follow-up
        # loop did. Both paths now write to the same place.
        mailbox_id = None
        if mailbox is not None:
            thread_id = case.get("subject_token") or case["id"]
            msg = await mailbox.send(
                case_id=case["id"],
                thread_id=thread_id,
                to_addr=recipient,
                from_addr="ar-agent@local.mailbox",
                subject=subject,
                body=final_draft,
                headers={"X-Ask-Id": ask_id, "X-Tactic": selected.tactic},
            )
            mailbox_id = msg["id"]
            tools.append("mailbox")
        # Real send (added 2026-08-04): same wiring as traced_loop.py --
        # get_email_sender() returns GraphEmailSender the moment
        # GRAPH_MAIL_* + EMAIL_FROM_ADDRESS are set.
        from app.services.email_sender import get_email_sender

        try:
            real_send_result = await get_email_sender().send(recipient, subject, f"<p>{final_draft}</p>", [])
            tools.append("email")
        except Exception as exc:  # noqa: BLE001
            real_send_result = {"success": False, "error": str(exc)}
        await ledger.append(
            case["id"],
            "outreach_sent",
            {
                "body": final_draft,
                "recipient": recipient,
                "tactic": selected.tactic,
                "ask_id": ask_id,
                "mailbox_id": mailbox_id,
                "real_send": real_send_result,
            },
            at=now.isoformat(),
            principles=["P4", "P12"],
        )
        consume_unanswered(budget)
        case["budget"] = budget.to_dict()
        dialogue = dict(case.get("dialogue") or {})
        dialogue["latest_outbound"] = final_draft
        dialogue["last_ask_id"] = ask_id
        dialogue["last_ask_tactic"] = selected.tactic
        case["dialogue"] = dialogue
        case["last_outreach_at"] = now.isoformat()
        # State after outreach
        if selected.objective == "verify_payment":
            case["state"] = "waiting_for_customer"
            principles.append("P1")
        elif selected.objective == "clarify_date":
            case["state"] = "waiting_for_customer"
            principles.append("P7")
        elif case.get("state") in ("customer_responded", "promise_missed", "outreach_ready", "overdue", "due"):
            case["state"] = "waiting_for_customer"
        case["next_action_at"] = (now + timedelta(days=policy.nudge_interval_days)).isoformat()
        if budget.exhausted():
            case["state"] = "escalation_required"
            principles.append("P3")

    goals = GoalStack.from_dict(context.get("goal_stack"))
    goals.selected_tactic = selected.tactic
    goals.current_objective = selected.objective
    case["goals"] = goals.to_dict()

    note = reflexion_note(case)
    if note:
        principles.append("P13")

    if regenerated:
        principles.append("P4")

    state_after = case.get("state")
    trace = _trace(
        case, trigger, state_before, state_after, cand_dicts, selected_d,
        judgment, sorted(set(principles)),
        f"Selected {selected.tactic} ({selected.objective}) score={selected.score}",
        context, now, policies=guard.policies_checked, tools=tools, reflexion=note,
    )
    await _persist(store, ledger, case, trace, sorted(set(principles)))
    return {"trace": trace, "case": case}


def _trace(
    case, trigger, before, after, candidates, selected, critic, principles, explanation,
    context, now, policies=None, tools=None, reflexion="",
) -> Dict[str, Any]:
    return {
        "id": str(uuid4()),
        "case_id": case.get("id"),
        "at": now.isoformat(),
        "trigger": trigger,
        "state_before": before,
        "state_after": after,
        "candidates_scored": candidates,
        "selected_action": selected,
        "critic_result": critic,
        "policies_checked": policies or [],
        "tools_executed": tools or [],
        "principles_fired": principles,
        "explanation": explanation,
        "reflexion_note": reflexion,
        "loop_phase": "schedule",
        "memory_facts": (context or {}).get("recalled_memory_facts") or [],
        "context_summary": {
            "goal_stack": (context or {}).get("goal_stack"),
            "uncertainty": (context or {}).get("uncertainty"),
            "budgets": (context or {}).get("budgets"),
            "world_dialogue_conflict": (context or {}).get("world_dialogue_conflict"),
        },
    }


async def _persist(store, ledger, case, trace, principles) -> None:
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
        last_decision=trace,
        customer_email=case.get("customer_email"),
        customer_name=case.get("customer_name"),
        target=case.get("target"),
    )
    await store.add_decision_trace(case["id"], trace)
    await ledger.append(
        case["id"],
        "decision",
        {"explanation": trace.get("explanation"), "principles_fired": principles, "trace_id": trace["id"]},
        at=trace["at"],
        principles=principles,
    )
