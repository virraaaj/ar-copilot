"""Coverage for the "customer asks for a human" handoff (2026-09-03).

Business requirement: the outbound disclosure line now appended to every
customer email ("...just reply and a member of the team will pick this up")
means customers WILL reply asking for a human, or asking outright whether
they're talking to a bot. When that happens the agent must (1) stop
chasing, (2) escalate to a human, (3) get the PM notified by email -- and
must never deny being automated.

The fix wires the previously-dead `ReplyType.HANDOFF` enum value end to
end: reply_interpreter.py detects the phrasing, llm_tools.py's
_KNOWN_REPLY_TYPES stops remapping an LLM-emitted "handoff" to "unknown",
and signal_ingestion.py routes it through the exact same mechanism the
hostile-reply fix (also 2026-09-03, see test_hostile_reply_escalation.py)
already established: transition(state, "hostile") -> the state machine's
hard rule to CaseState.ESCALATION_REQUIRED -> goals.choose_objective's
existing escalation branch -> escalate_handoff. No second escalation path,
no world.status write (this is a dialogue fact, not world truth).

PM notification is not re-implemented here: executor.run_case_loop's
hard-stop check (`context.get("goal_stack", {}).get("current_objective")
== "escalate_handoff"`) already fires send_escalation_notice(...) for any
case whose goal stack lands on escalate_handoff, before any candidate
chase action is even planned -- so a handoff reply gets the PM email and
never a chase send, for free, via the same path the hostile-reply fix
verified. Adding a second notification call here would double-send.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.outcome_agent.domain.dialogue_model import DialogueSnapshot
from app.outcome_agent.domain.goals import build_goal_stack, choose_objective
from app.outcome_agent.domain.types import AutonomyBudget, Uncertainty
from app.outcome_agent.domain.world_model import WorldSnapshot
from app.outcome_agent.loop.reply_interpreter import DEFAULT_INTERPRETER
from app.outcome_agent.loop.signal_ingestion import apply_reply_signal

CONFIDENT = Uncertainty(confidence=1.0, needs_clarification=False, unclear_fields=[], note="")

CHASE_TACTICS = {"polite_outreach", "soft_nudge", "firm_reminder"}

HANDOFF_PHRASES = [
    "Can I speak to a human please?",
    "I'd like to speak to a person about this.",
    "Please let me talk to a real person.",
    "Is this a bot?",
    "Are you an AI?",
    "Are you a robot?",
    "Am I talking to a human right now?",
    "I want a person, not an automated message.",
    "Can you put me through to someone who can help?",
    "Can someone call me about this invoice?",
    "Who am I speaking to?",
]

ORDINARY_PHRASES = [
    "We'll pay on the 15th.",
    "I'll check with our finance person and come back to you.",
    "Our accounts person is away this week.",
]


class _FakeLedger:
    """Minimal stand-in for EventLedger -- apply_reply_signal only calls
    .append(), and none of these tests need to inspect what was logged."""

    async def append(self, *args, **kwargs) -> None:
        return None


def _make_case() -> dict:
    return {
        "id": "case-1",
        "case_id": "case-1",
        "invoice_no": "INV-1",
        "state": "waiting_for_customer",
        "target": "customer",
        "world": {
            "invoice_no": "INV-1",
            "case_id": "case-1",
            "balance_due": 1000.0,
            "status": "open",
        },
        "dialogue": {},
        "budget": {},
        "commitments": [],
        "blockers": [],
    }


@pytest.mark.parametrize("phrase", HANDOFF_PHRASES)
def test_handoff_phrases_classify_as_handoff(phrase):
    interp = DEFAULT_INTERPRETER.interpret(phrase, now=datetime(2026, 9, 3))
    assert interp.reply_type == "handoff"


@pytest.mark.parametrize("phrase", ORDINARY_PHRASES)
def test_ordinary_replies_do_not_misfire_as_handoff(phrase):
    """"person" appears innocently in normal AR replies -- the detection
    regex must require a human-directed verb or bot question around it,
    not just the bare word."""
    interp = DEFAULT_INTERPRETER.interpret(phrase, now=datetime(2026, 9, 3))
    assert interp.reply_type != "handoff"


@pytest.mark.asyncio
async def test_handoff_reply_sets_escalation_required_state_not_a_chase():
    case = _make_case()
    interp = await apply_reply_signal(
        case, "Can I speak to a human please? I don't want to deal with a bot.",
        _FakeLedger(), now=datetime(2026, 9, 3),
    )
    assert interp.reply_type == "handoff"
    assert case["state"] == "escalation_required"
    # Dialogue fact, not world truth (world_model.py:1) -- must not be
    # conflated with e.g. "disputed".
    assert case["world"]["status"] == "open"


def test_handoff_state_routes_to_escalate_handoff_objective():
    world = WorldSnapshot.from_dict(
        {"invoice_no": "INV-1", "case_id": "case-1", "balance_due": 1000.0, "status": "open"}
    )
    dialogue = DialogueSnapshot(latest_inbound="Can I speak to a human please?")
    objective, rationale = choose_objective(
        "escalation_required",
        world,
        dialogue,
        AutonomyBudget(),
        CONFIDENT,
        active_commitment_type=None,
        contact_target="customer",
    )
    assert objective == "escalate_handoff"
    assert objective != "establish_contact"


def test_handoff_state_never_selects_a_customer_facing_chase_tactic():
    world = WorldSnapshot.from_dict(
        {"invoice_no": "INV-1", "case_id": "case-1", "balance_due": 1000.0, "status": "open"}
    )
    dialogue = DialogueSnapshot(latest_inbound="Can I speak to a human please?")
    stack = build_goal_stack(
        "escalation_required",
        world,
        dialogue,
        AutonomyBudget(),
        CONFIDENT,
        active_commitment_type=None,
        contact_target="customer",
    )
    assert stack.current_objective == "escalate_handoff"
    assert stack.selected_tactic == "escalation_pack"
    assert stack.selected_tactic not in CHASE_TACTICS
