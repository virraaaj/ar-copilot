"""Tests: chase_parser.py (added 2026-07-20, Phase C1). ScriptedLLM
returns pre-built forced-tool-call responses -- same ScriptedLLM idiom
used across the rest of this suite (test_agent_loop.py, test_teams_bot.py)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.chase_parser import ParsedReply, parse_chase_reply


def make_tool_call(name: str, arguments: dict) -> SimpleNamespace:
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def make_message(tool_calls=None) -> SimpleNamespace:
    return SimpleNamespace(content=None, tool_calls=tool_calls)


class ScriptedLLM:
    def __init__(self, response, tokens=99):
        self._response = response
        self._tokens = tokens
        self.calls = []

    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        return self._response, self._tokens


class RaisingLLM:
    async def chat(self, messages, tools=None, tool_choice="auto", return_usage=False):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_commitment_date_high_confidence():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "commitment_date", "confidence": "high", "promised_date": "2026-08-01",
        })
    ]))

    result = await parse_chase_reply(llm, "We'll pay by August 1st.", {"today": "2026-07-20"})

    assert result == ParsedReply(intent="commitment_date", confidence="high",
                                  promised_date="2026-08-01", customer_contact_email=None,
                                  raw_text="We'll pay by August 1st.", tokens_used=99)


@pytest.mark.asyncio
async def test_forces_the_tool_choice():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "no_commitment", "confidence": "low"})
    ]))

    await parse_chase_reply(llm, "will check", {})

    assert llm.calls[0]["tool_choice"] == {"type": "function", "function": {"name": "record_reply_interpretation"}}
    assert llm.calls[0]["tools"][0]["function"]["name"] == "record_reply_interpretation"


@pytest.mark.asyncio
async def test_handoff_to_customer_with_email():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "handoff_to_customer", "confidence": "high", "customer_contact_email": "bob@customer.com",
        })
    ]))

    result = await parse_chase_reply(llm, "Ask bob@customer.com directly.", {})

    assert result.intent == "handoff_to_customer"
    assert result.customer_contact_email == "bob@customer.com"


@pytest.mark.asyncio
async def test_handoff_with_invalid_email_drops_it():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "handoff_to_customer", "confidence": "high", "customer_contact_email": "not-an-email",
        })
    ]))

    result = await parse_chase_reply(llm, "talk to the customer", {})

    assert result.customer_contact_email is None


@pytest.mark.asyncio
async def test_claims_paid():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "claims_paid", "confidence": "high"})
    ]))

    result = await parse_chase_reply(llm, "This was already paid last week.", {})

    assert result.intent == "claims_paid"
    assert result.promised_date is None


@pytest.mark.asyncio
async def test_dispute():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "dispute", "confidence": "high"})
    ]))

    result = await parse_chase_reply(llm, "We don't agree with this invoice amount.", {})

    assert result.intent == "dispute"


@pytest.mark.asyncio
async def test_vague_timing_is_no_commitment_not_a_guessed_date():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "no_commitment", "confidence": "low"})
    ]))

    result = await parse_chase_reply(llm, "Should be soon.", {})

    assert result.intent == "no_commitment"
    assert result.promised_date is None


@pytest.mark.asyncio
async def test_commitment_date_claimed_but_missing_date_falls_back_to_unclear():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "commitment_date", "confidence": "high"})
    ]))

    result = await parse_chase_reply(llm, "we'll pay soon-ish", {})

    assert result.intent == "unclear"
    assert result.confidence == "low"


@pytest.mark.asyncio
async def test_commitment_date_with_malformed_date_string_falls_back_to_unclear():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {
            "intent": "commitment_date", "confidence": "high", "promised_date": "next Friday",
        })
    ]))

    result = await parse_chase_reply(llm, "next Friday", {})

    assert result.intent == "unclear"


@pytest.mark.asyncio
async def test_no_tool_call_falls_back_to_unclear():
    llm = ScriptedLLM(make_message(tool_calls=None))

    result = await parse_chase_reply(llm, "anything", {})

    assert result == ParsedReply(intent="unclear", confidence="low", raw_text="anything", tokens_used=99)


@pytest.mark.asyncio
async def test_malformed_tool_call_arguments_falls_back_to_unclear():
    bad_call = SimpleNamespace(function=SimpleNamespace(name="record_reply_interpretation", arguments="{not json"))
    llm = ScriptedLLM(make_message(tool_calls=[bad_call]))

    result = await parse_chase_reply(llm, "anything", {})

    assert result.intent == "unclear"


@pytest.mark.asyncio
async def test_invalid_intent_value_falls_back_to_unclear():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "something_made_up", "confidence": "high"})
    ]))

    result = await parse_chase_reply(llm, "anything", {})

    assert result.intent == "unclear"


@pytest.mark.asyncio
async def test_missing_confidence_defaults_to_low():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "no_commitment"})
    ]))

    result = await parse_chase_reply(llm, "anything", {})

    assert result.confidence == "low"


@pytest.mark.asyncio
async def test_llm_exception_falls_back_to_unclear_not_raised():
    result = await parse_chase_reply(RaisingLLM(), "anything", {})

    assert result == ParsedReply(intent="unclear", confidence="low", raw_text="anything")


@pytest.mark.asyncio
async def test_prompt_includes_todays_date_and_reply_text():
    llm = ScriptedLLM(make_message(tool_calls=[
        make_tool_call("record_reply_interpretation", {"intent": "no_commitment", "confidence": "low"})
    ]))

    await parse_chase_reply(llm, "the actual reply text", {"today": "2026-07-20", "invoice_no": "INV-99", "target": "pm"})

    sent = llm.calls[0]["messages"]
    system_content = sent[0]["content"]
    assert "2026-07-20" in system_content
    assert "INV-99" in system_content
    assert sent[1]["content"] == "the actual reply text"
