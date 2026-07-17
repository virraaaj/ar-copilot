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
    ):
        """One round-trip to the model. Returns the raw message object so the
        caller can inspect tool_calls vs content."""
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
        return response.choices[0].message


_service: Optional[AzureOpenAIService] = None


def get_llm() -> AzureOpenAIService:
    global _service
    if _service is None:
        _service = AzureOpenAIService()
    return _service
