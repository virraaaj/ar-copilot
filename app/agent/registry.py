"""
Tool registry — every tool is classified read or write at registration time.
This is the actual enforcement point for PLAN.md §6.2 ("read/write separation
— enforced by the registry, not the prompt"): a viewer role's tool list
literally does not contain write tools, so the model can't be tricked into
calling one no matter what the prompt says.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Optional

from app.services.backend_client import BackendClient


class ToolKind(str, enum.Enum):
    READ = "read"
    WRITE = "write"


Handler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    kind: ToolKind
    schema: Dict[str, Any]  # OpenAI function-calling schema (name/description/parameters)
    handler: Handler
    # Roles beyond "admin" allowed to call a WRITE tool. Ignored for READ
    # tools (every role sees every read tool). Empty by default -- a write
    # tool registered with no extra_roles is admin-only.
    extra_roles: FrozenSet[str] = field(default_factory=frozenset)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(
        self,
        name: str,
        kind: ToolKind,
        schema: Dict[str, Any],
        handler: Handler,
        extra_roles: FrozenSet[str] = frozenset(),
    ) -> None:
        if name in self._tools:
            raise ValueError(f"Tool already registered: {name}")
        if schema.get("name") != name:
            raise ValueError(f"Schema name '{schema.get('name')}' doesn't match tool name '{name}'")
        self._tools[name] = Tool(name=name, kind=kind, schema=schema, handler=handler, extra_roles=extra_roles)

    def register_many(
        self,
        kind: ToolKind,
        schemas: List[Dict[str, Any]],
        handlers: Dict[str, Handler],
        extra_roles: FrozenSet[str] = frozenset(),
    ) -> None:
        for schema in schemas:
            name = schema["name"]
            self.register(name, kind, schema, handlers[name], extra_roles=extra_roles)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def for_role(self, role: str) -> List[Tool]:
        """admin -> every registered tool. Any other role -> every read tool,
        plus write tools that explicitly list this role in extra_roles."""
        if role == "admin":
            return list(self._tools.values())
        return [t for t in self._tools.values() if t.kind is ToolKind.READ or role in t.extra_roles]

    def openai_tools(self, role: str) -> List[Dict[str, Any]]:
        """Tool list in the shape the OpenAI/Azure OpenAI tools= param expects,
        scoped to what this role is allowed to call."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.schema["name"],
                    "description": t.schema["description"],
                    "parameters": t.schema["parameters"],
                },
            }
            for t in self.for_role(role)
        ]

    def is_permitted(self, name: str, role: str) -> bool:
        tool = self.get(name)
        if tool is None:
            return False
        return tool in self.for_role(role)


def build_default_registry(read_schemas: List[Dict[str, Any]], read_handlers: Dict[str, Handler]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(ToolKind.READ, read_schemas, read_handlers)
    return registry
