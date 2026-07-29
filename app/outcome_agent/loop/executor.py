"""Action executor — outbox, schedule, escalate, state updates."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.outcome_agent.domain.budgets import consume_unanswered, hard_stop_reason
from app.outcome_agent.domain.types import AutonomyBudget, EscalationPack, GoalStack
from app.outcome_agent.loop.communication import DEFAULT_GENERATOR
from app.outcome_agent.loop.critic import critique_with_regen
from app.outcome_agent.loop.guardrails import check_before_send
from app.outcome_agent.loop.next_best_action import plan_next_action
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
) -> Dict[str, Any]:
    """Full observe→plan→simulate→critic→guard→execute for one case."""
    from app.outcome_agent.config.policies import policy_from_settings

    policy = policy_from_settings(settings)
    state_before = case.get("state")
    principles: List[str] = ["P12", "P8", "P9", "P4"]

    # Hard terminals — no outreach
    world = case.get("world") or {}
    if float(world.get("balance_due") or 0) <= 0 or world.get("status") == "paid" or state_before == "paid":
        case["state"] = "paid"
        await store.update(case["id"], state="paid", next_action_at=None, world=case["world"])
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
        return {"trace": trace, "escalated": True}

    candidates, selected = plan_next_action(context)
    cand_dicts = [c.to_dict() for c in candidates]
    selected_d = selected.to_dict()

    # S7 harness: force bad draft through critic
    if force_bad_draft:
        selected_d["draft_text"] = force_bad_draft
        selected_d["tactic"] = "force_threat"

    draft = selected_d.get("draft_text") or DEFAULT_GENERATOR.generate(
        case, selected.tactic, selected.objective
    )

    def _regen() -> str:
        # Safe regenerated template
        return DEFAULT_GENERATOR.generate(case, "clarify_ask", "clarify_date")

    final_draft, critic_result = critique_with_regen(draft, context, selected_d, _regen)
    selected_d["draft_text"] = final_draft

    if critic_result.requires_human_review and not critic_result.passed:
        principles.append("P4")
        trace = _trace(
            case, trigger, state_before, state_before, cand_dicts, selected_d,
            critic_result.to_dict(), principles,
            "Critic blocked send — human review", context, now,
            reflexion=reflexion_note(case),
        )
        await store.add_decision_trace(case["id"], trace)
        await ledger.append(case["id"], "critic_blocked", {"checks": critic_result.checks}, at=now.isoformat(), principles=["P4"])
        return {"trace": trace, "blocked": True}

    guard = check_before_send(
        case,
        final_draft,
        now=now,
        dry_run=policy.dry_run,
        allowlist=policy.to_address_allowlist,
        recipient=case.get("customer_email"),
    )
    if not guard.allowed:
        trace = _trace(
            case, trigger, state_before, state_before, cand_dicts, selected_d,
            critic_result.to_dict(), principles + ["P4"],
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
    else:
        recipient = case.get("customer_email") or case.get("pm_email") or "demo@example.com"
        subject = f"[{case.get('subject_token')}] Invoice {case.get('invoice_no')}"
        await store.add_outbox(
            case["id"],
            final_draft,
            channel="email",
            recipient=recipient,
            subject=subject,
            dry_run=policy.dry_run,
            meta={"tactic": selected.tactic, "ask_id": ask_id},
        )
        tools.append("outbox")
        kind = "dry_run_send" if policy.dry_run else "outreach_sent"
        await ledger.append(
            case["id"],
            kind,
            {"body": final_draft, "recipient": recipient, "tactic": selected.tactic, "ask_id": ask_id},
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

    if critic_result.regenerated:
        principles.append("P4")

    state_after = case.get("state")
    trace = _trace(
        case, trigger, state_before, state_after, cand_dicts, selected_d,
        critic_result.to_dict(), sorted(set(principles)),
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
    )
    await store.add_decision_trace(case["id"], trace)
    await ledger.append(
        case["id"],
        "decision",
        {"explanation": trace.get("explanation"), "principles_fired": principles, "trace_id": trace["id"]},
        at=trace["at"],
        principles=principles,
    )
