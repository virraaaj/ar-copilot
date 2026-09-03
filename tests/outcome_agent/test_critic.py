from app.outcome_agent.loop.critic import critique, critique_with_regen


def test_critic_blocks_banned_language():
    draft = "Pay now or we will sue and send this to a collections agency"
    result = critique(draft, {"invoice_no": "INV-1", "world": {}, "goal_stack": {}, "failed_asks": []}, {"tactic": "firm_reminder", "objective": "obtain_commitment", "score": 1})
    assert not result.passed
    assert any(c["name"] == "language_guardrail" and not c["pass"] for c in result.checks)


def test_critic_catches_indirect_document_promise():
    # Regression for 2026-08-18 (found via guided-demo review): this
    # system can only send/receive plain-text email in the same thread --
    # it cannot actually fulfil an open-ended "we'll handle it" request.
    # Neither half of this sentence matched the old regex alone ("need a
    # copy" isn't a promise verb; "we'll handle it" names no capability),
    # so the combination slipped through as a real, live-sent email.
    draft = (
        "Could you confirm the expected payment date for DEMO-INV-1001? "
        "If you need a copy of the invoice, please reply to this message and we'll handle it from here."
    )
    result = critique(draft, {"invoice_no": "DEMO-INV-1001", "world": {}, "goal_stack": {}, "failed_asks": []}, {"tactic": "firm_reminder", "objective": "obtain_commitment", "score": 1})
    assert not result.passed
    assert any(c["name"] == "no_unsupported_promise" and not c["pass"] for c in result.checks)


def test_critic_regenerate_once():
    bad = "Pay now or we will sue"
    ctx = {
        "invoice_no": "INV-1",
        "world": {"balance_due": 10},
        "goal_stack": {"current_objective": "clarify_date"},
        "failed_asks": [],
    }
    selected = {"tactic": "force_threat", "objective": "obtain_commitment", "score": 1}

    def regen():
        return "Thanks for the note on INV-1. Could you confirm one concrete payment date?"

    final, result = critique_with_regen(bad, ctx, selected, regen)
    assert result.regenerated
    assert result.passed
    assert "sue" not in final.lower()
