"""
CLI harness for fast agent iteration, no Teams or UI needed.

  python -m app.cli "which project has the most money stuck past 60 days?"

Requires a real AZURE_OPENAI_KEY in .env — this exercises the live model.
Blocked until that key arrives (see PLAN.md §7); the loop/tools/registry are
already unit-tested against a mocked LLM in tests/test_agent_loop.py, so this
script is purely for manual, real-model iteration once the key lands.
"""
from __future__ import annotations

import asyncio
import sys

from app.agent.loop import AgentLoop
from app.agent.setup import build_registry
from app.services.azure_openai import get_llm
from app.services.backend_client import get_backend_client


async def main(question: str, role: str = "viewer") -> None:
    backend = get_backend_client()
    registry = build_registry()
    llm = get_llm()
    loop = AgentLoop(llm=llm, registry=registry, backend_client=backend)

    try:
        result = await loop.run(question, role=role)
        print(result.answer)
        print(f"\n--- {len(result.tool_calls)} tool call(s), truncated={result.truncated} ---")
        for tc in result.tool_calls:
            print(f"  {tc.name}({tc.arguments}) -> permitted={tc.permitted}")
    finally:
        await backend.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: python -m app.cli "your question"')
    asyncio.run(main(sys.argv[1]))
