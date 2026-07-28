"""Tests: chase_composer.py (added 2026-07-22). ScriptedLLM returns
pre-built (message, tokens) tuples matching AzureOpenAIService's
return_usage=True shape -- same ScriptedLLM idiom as the rest of this
suite."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.chase_composer import compose_and_evaluate, compose_message


class ScriptedLLM:
    def __init__(self, message_content, tokens=42, raise_exc=None):
        self._content = message_content
        self._tokens = tokens
        self._raise = raise_exc
        self.calls = []

    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
        self.calls.append(messages)
        if self._raise:
            raise self._raise
        return SimpleNamespace(content=self._content), self._tokens


def base_chase(**overrides):
    chase = {"invoice_no": "INV-1", "case_key": "CK-1", "missed_count": 0, "nudge_count": 0}
    chase.update(overrides)
    return chase


@pytest.mark.asyncio
async def test_composes_a_message_and_returns_tokens():
    llm = ScriptedLLM("Hey there -- just checking in on invoice INV-1, which is overdue.", tokens=88)

    text, tokens = await compose_message(llm, "outreach", base_chase(), "Invoice INV-1 is now overdue.")

    assert text == "Hey there -- just checking in on invoice INV-1, which is overdue."
    assert tokens == 88


@pytest.mark.asyncio
async def test_falls_back_to_template_on_llm_exception():
    llm = ScriptedLLM(None, raise_exc=RuntimeError("boom"))

    text, tokens = await compose_message(llm, "outreach", base_chase(), "Invoice INV-1 is now overdue.")

    assert text == "Invoice INV-1 is now overdue."
    assert tokens == 0


@pytest.mark.asyncio
async def test_falls_back_to_template_on_empty_content():
    llm = ScriptedLLM("   ", tokens=10)

    text, tokens = await compose_message(llm, "outreach", base_chase(), "Invoice INV-1 is now overdue.")

    assert text == "Invoice INV-1 is now overdue."
    assert tokens == 10  # still charged -- a real call was made, just an unusable answer


@pytest.mark.asyncio
async def test_falls_back_to_template_on_over_length_output():
    llm = ScriptedLLM("x" * 1000, tokens=500)

    text, tokens = await compose_message(llm, "outreach", base_chase(), "Invoice INV-1 is now overdue.")

    assert text == "Invoice INV-1 is now overdue."


@pytest.mark.asyncio
async def test_falls_back_when_composed_message_invents_a_date():
    template = "Invoice INV-1 is now overdue. Is there a payment date I should be tracking?"
    llm = ScriptedLLM("We'll expect payment by August 15th, thanks!", tokens=50)

    text, tokens = await compose_message(llm, "outreach", base_chase(), template)

    assert text == template


@pytest.mark.asyncio
async def test_accepts_composed_message_that_repeats_the_templates_own_date_in_prose_form():
    template = "The payment date you gave (2026-08-15) has passed and it's still unpaid."
    llm = ScriptedLLM("Just checking in -- the August 15 payment date has passed and we still show this as unpaid.", tokens=60)

    text, tokens = await compose_message(llm, "rechase", base_chase(), template)

    assert "August 15" in text


@pytest.mark.asyncio
async def test_accepts_composed_message_with_no_dates_at_all():
    template = "Invoice INV-1 is now overdue. Is there a payment date I should be tracking?"
    llm = ScriptedLLM("Hi! Wanted to follow up on this overdue invoice -- do you have an update?", tokens=40)

    text, tokens = await compose_message(llm, "outreach", base_chase(), template)

    assert text == "Hi! Wanted to follow up on this overdue invoice -- do you have an update?"


@pytest.mark.asyncio
async def test_prompt_includes_template_as_required_substance_and_context():
    llm = ScriptedLLM("some message", tokens=10)
    chase = base_chase(invoice_no="INV-42", missed_count=2, nudge_count=1)

    await compose_message(llm, "nudge", chase, "Following up on invoice INV-42.")

    system_content = llm.calls[0][0]["content"]
    assert "INV-42" in system_content or "Following up on invoice INV-42." in system_content
    assert "2 missed commitment" in system_content
    assert "1 prior follow-up" in system_content


@pytest.mark.asyncio
async def test_handles_llm_that_ignores_return_usage_and_returns_bare_message():
    class BareLLM:
        async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
            return SimpleNamespace(content="a bare message with no usage tuple")

    text, tokens = await compose_message(BareLLM(), "outreach", base_chase(), "template text")

    assert text == "a bare message with no usage tuple"
    assert tokens == 0


# ---- compose_and_evaluate (added 2026-07-28, spec §6.14) -------------------


class QueueLLM:
    """Returns pre-scripted (content, tokens) responses in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
        self.calls.append(messages)
        content, tokens = self._responses.pop(0)
        return SimpleNamespace(content=content), tokens


@pytest.mark.asyncio
async def test_compose_and_evaluate_passes_a_good_message_on_first_try():
    llm = QueueLLM([("Hi -- checking in on invoice INV-1, any update on payment?", 50)])

    text, tokens, evaluation, needs_review = await compose_and_evaluate(
        llm, "outreach", base_chase(), "Invoice INV-1 is now overdue."
    )

    assert text == "Hi -- checking in on invoice INV-1, any update on payment?"
    assert tokens == 50
    assert evaluation is not None and evaluation.passed is True
    assert needs_review is False
    assert len(llm.calls) == 1  # no regeneration needed


@pytest.mark.asyncio
async def test_compose_and_evaluate_regenerates_once_when_evaluation_fails():
    llm = QueueLLM([
        ("Just checking in, any update?", 30),  # fails: no invoice reference
        ("Hi -- any update on invoice INV-1?", 40),  # passes on retry
    ])

    text, tokens, evaluation, needs_review = await compose_and_evaluate(
        llm, "outreach", base_chase(), "Invoice INV-1 is now overdue."
    )

    assert text == "Hi -- any update on invoice INV-1?"
    assert tokens == 70  # both attempts charged
    assert evaluation is not None and evaluation.passed is True
    assert needs_review is False
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_compose_and_evaluate_falls_back_to_template_and_flags_review_after_two_failures():
    llm = QueueLLM([
        ("Just checking in, any update?", 30),
        ("Still following up, anything new?", 20),
    ])
    template = "Invoice INV-1 is now overdue."

    text, tokens, evaluation, needs_review = await compose_and_evaluate(llm, "outreach", base_chase(), template)

    assert text == template  # safe fallback, never the twice-failed AI text
    assert tokens == 50
    assert needs_review is True
    assert evaluation is not None and evaluation.passed is False
    assert len(llm.calls) == 2  # never a third attempt
