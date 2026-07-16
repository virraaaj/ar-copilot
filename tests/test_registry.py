"""Phase 1 tests: tool registry read/write enforcement."""
from __future__ import annotations

from app.agent.registry import ToolKind, ToolRegistry


async def _noop_handler(client, **kwargs):
    return {"ok": True}


def _schema(name: str) -> dict:
    return {"name": name, "description": "test tool", "parameters": {"type": "object", "properties": {}}}


def test_viewer_gets_only_read_tools():
    registry = ToolRegistry()
    registry.register("read_thing", ToolKind.READ, _schema("read_thing"), _noop_handler)
    registry.register("write_thing", ToolKind.WRITE, _schema("write_thing"), _noop_handler)

    viewer_tools = {t.name for t in registry.for_role("viewer")}
    admin_tools = {t.name for t in registry.for_role("admin")}

    assert viewer_tools == {"read_thing"}
    assert admin_tools == {"read_thing", "write_thing"}


def test_is_permitted_respects_role():
    registry = ToolRegistry()
    registry.register("read_thing", ToolKind.READ, _schema("read_thing"), _noop_handler)
    registry.register("write_thing", ToolKind.WRITE, _schema("write_thing"), _noop_handler)

    assert registry.is_permitted("read_thing", "viewer") is True
    assert registry.is_permitted("write_thing", "viewer") is False
    assert registry.is_permitted("write_thing", "admin") is True
    assert registry.is_permitted("nonexistent_tool", "admin") is False


def test_openai_tools_shape_matches_function_calling_schema():
    registry = ToolRegistry()
    registry.register("read_thing", ToolKind.READ, _schema("read_thing"), _noop_handler)

    tools = registry.openai_tools("viewer")

    assert tools == [
        {
            "type": "function",
            "function": {
                "name": "read_thing",
                "description": "test tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def test_duplicate_registration_rejected():
    registry = ToolRegistry()
    registry.register("read_thing", ToolKind.READ, _schema("read_thing"), _noop_handler)
    try:
        registry.register("read_thing", ToolKind.READ, _schema("read_thing"), _noop_handler)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_schema_name_mismatch_rejected():
    registry = ToolRegistry()
    try:
        registry.register("read_thing", ToolKind.READ, _schema("different_name"), _noop_handler)
        assert False, "expected ValueError"
    except ValueError:
        pass
