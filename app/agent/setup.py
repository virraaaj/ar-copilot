"""Wires the default tool registry (Phase 1: read-only tools only)."""
from __future__ import annotations

from app.agent import tools_read
from app.agent.registry import ToolRegistry, ToolKind


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(ToolKind.READ, tools_read.SCHEMAS, tools_read.HANDLERS)
    return registry
