"""Wires the default tool registry (Phase 1 + Phase 1B + Phase 3)."""
from __future__ import annotations

from app.agent import tools_documents, tools_read, tools_write
from app.agent.registry import ToolRegistry, ToolKind


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(ToolKind.READ, tools_read.SCHEMAS, tools_read.HANDLERS)
    registry.register_many(ToolKind.READ, tools_documents.SCHEMAS, tools_documents.HANDLERS)
    registry.register_many(ToolKind.WRITE, tools_write.SCHEMAS, tools_write.HANDLERS, extra_roles=frozenset({"pm"}))
    return registry
