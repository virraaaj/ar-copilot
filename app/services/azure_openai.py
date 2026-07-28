"""
Azure OpenAI client wrapper — the ONLY AI provider (see PLAN.md §3: no
non-Azure AI services). Async, since the rest of this app is async.

Blocked on a real AZURE_OPENAI_KEY as of 2026-07-16 — the surrounding agent
loop/tools/tests are all built and unit-tested against a mocked version of
this class. Swapping in the real key is a .env change only; no code changes
needed once it arrives.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from openai import AsyncAzureOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)


class TokenUsage(int):
    """A plain int (the total token count) that also carries the prompt/
    completion split (added 2026-07-28, per the boss's question about
    whether the tracked token count is input+output combined -- it was,
    but only the combined total was ever stored). Subclassing int rather
    than adding a new return value keeps every existing call site working
    unchanged: `chase_store.increment_tokens(id, tokens)`, `tokens_used=
    tokens` on ParsedReply, arithmetic, SQL param binding, f-string
    formatting -- all of that already treats this as a plain int and
    still does. Only code that explicitly wants the split reads
    `.prompt`/`.completion`."""

    def __new__(cls, total: int, prompt: int = 0, completion: int = 0) -> "TokenUsage":
        obj = super().__new__(cls, total)
        obj.prompt = prompt
        obj.completion = completion
        return obj


class AzureOpenAIService:
    def __init__(self) -> None:
        s = get_settings()
        self._deployment = s.AZURE_OPENAI_DEPLOYMENT_NAME
        self._client = AsyncAzureOpenAI(
            azure_endpoint=s.AZURE_OPENAI_ENDPOINT.strip(),
            api_key=s.AZURE_OPENAI_KEY.strip(),
            api_version=s.AZURE_OPENAI_API_VERSION,
            max_retries=3,
            timeout=120.0,
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        return_usage: bool = False,
    ):
        """One round-trip to the model. Returns the raw message object so the
        caller can inspect tool_calls vs content.

        return_usage=True (added 2026-07-22, for the chase engine's
        per-invoice token tracking) additionally returns the call's token
        count as (message, tokens), where tokens is a TokenUsage -- a
        plain int (prompt_tokens + completion_tokens, same as before) that
        also carries the .prompt/.completion split. Defaults to False and
        every existing caller (AgentLoop, etc.) never passes it, so this
        is purely additive -- no existing call site's return shape
        changes."""
        # No `temperature` override -- confirmed live against the real
        # deployment (gpt-5-mini): reasoning-family GPT-5 models reject any
        # non-default temperature ("Only the default (1) value is
        # supported"), unlike GPT-4-class models. Omitting it lets the API
        # use that default rather than hardcoding a value that only works
        # for some model families.
        kwargs: Dict[str, Any] = {
            "model": self._deployment,  # Azure uses the deployment name here
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        if return_usage:
            usage = response.usage
            tokens = (
                TokenUsage(usage.total_tokens, usage.prompt_tokens, usage.completion_tokens)
                if usage else TokenUsage(0, 0, 0)
            )
            return message, tokens
        return message


_service: Optional[AzureOpenAIService] = None


def get_llm() -> AzureOpenAIService:
    global _service
    if _service is None:
        _service = AzureOpenAIService()
    return _service
