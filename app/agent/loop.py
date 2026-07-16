"""
Tool-calling agent loop: system prompt -> tool calls -> ... -> final answer.

PLAN.md §5 Phase 1 requirements this implements:
  - max 8 tool-call rounds, then answer with what it has
  - the registry (not the prompt) decides what a role may call; an
    unpermitted tool call gets an explicit rejection fed back to the model,
    never silently dropped
  - every tool call (including reads) is recorded, for the audit log
    (guardrails spec §6.5 — the audit sink itself lands in Phase 3)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.registry import ToolRegistry
from app.services.backend_client import BackendClient

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8


@dataclass
class ToolCallRecord:
    name: str
    arguments: Dict[str, Any]
    result: Any
    permitted: bool


@dataclass
class AgentResult:
    answer: str
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    truncated: bool = False


class LLMClient:
    """Structural type the loop depends on — anything with an async .chat()
    matching this signature works (AzureOpenAIService today; a mock in
    tests)."""

    async def chat(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, tool_choice: str = "auto"):
        raise NotImplementedError


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        backend_client: BackendClient,
        max_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._backend = backend_client
        self._max_rounds = max_rounds

    async def run(
        self,
        user_message: str,
        role: str = "viewer",
        history: Optional[List[Dict[str, Any]]] = None,
        on_tool_call: Optional[Callable[[ToolCallRecord], Awaitable[None]]] = None,
    ) -> AgentResult:
        """`on_tool_call`, if given, is awaited after each tool call resolves
        — this is what lets the web channel (Phase 2) stream progress over
        SSE without needing token-level streaming from the LLM itself."""
        messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        tool_calls_log: List[ToolCallRecord] = []
        tools_schema = self._registry.openai_tools(role)

        for round_num in range(self._max_rounds):
            message = await self._llm.chat(messages, tools=tools_schema or None)

            tool_calls = getattr(message, "tool_calls", None)
            if not tool_calls:
                content = getattr(message, "content", None) or ""
                return AgentResult(answer=content, tool_calls=tool_calls_log)

            messages.append(
                {
                    "role": "assistant",
                    "content": getattr(message, "content", None),
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if not self._registry.is_permitted(name, role):
                    result: Any = {
                        "error": f"Tool '{name}' is not permitted for role '{role}'."
                    }
                    permitted = False
                    logger.warning("Rejected unpermitted tool call: %s (role=%s)", name, role)
                else:
                    tool = self._registry.get(name)
                    permitted = True
                    try:
                        result = await tool.handler(self._backend, **args)
                    except Exception as exc:  # noqa: BLE001 — surfaced to the model, not swallowed
                        logger.warning("Tool '%s' raised: %s", name, exc)
                        result = {"error": str(exc)}

                record = ToolCallRecord(name=name, arguments=args, result=result, permitted=permitted)
                tool_calls_log.append(record)
                if on_tool_call is not None:
                    await on_tool_call(record)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, default=str),
                    }
                )

        # Ran out of rounds — answer with whatever the model has, flagged truncated.
        message = await self._llm.chat(messages, tools=None)
        content = getattr(message, "content", None) or "I wasn't able to fully answer within the tool-call budget."
        return AgentResult(answer=content, tool_calls=tool_calls_log, truncated=True)
