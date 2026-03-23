"""Unit tests for ToolRegistry."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tools.registry import ToolDefinition, ToolRegistry


def make_registry(*tools):
    r = ToolRegistry()
    for t in tools:
        r.register(t)
    return r


def make_tool(name="my_tool", category="data", handler=None):
    return ToolDefinition(
        name=name,
        description=f"Description for {name}",
        parameters={
            "type": "object",
            "properties": {"foo": {"type": "string"}},
            "required": ["foo"],
        },
        handler=handler or AsyncMock(return_value={"ok": True}),
        category=category,
    )


# ── Registration ───────────────────────────────────────────────────────────────

def test_register_and_get_all():
    t1 = make_tool("tool_a")
    t2 = make_tool("tool_b")
    r = make_registry(t1, t2)
    assert len(r.get_all()) == 2
    names = {t.name for t in r.get_all()}
    assert names == {"tool_a", "tool_b"}


def test_register_overwrites_duplicate():
    r = ToolRegistry()
    t1 = make_tool("t", handler=AsyncMock(return_value={"v": 1}))
    t2 = make_tool("t", handler=AsyncMock(return_value={"v": 2}))
    r.register(t1)
    r.register(t2)
    assert len(r.get_all()) == 1
    # Second registration wins
    assert r._tools["t"] is t2


# ── Schema conversion ──────────────────────────────────────────────────────────

def test_to_anthropic_format():
    r = make_registry(make_tool("alpha"), make_tool("beta"))
    schemas = r.to_anthropic_format()
    assert len(schemas) == 2
    for s in schemas:
        assert "name" in s
        assert "description" in s
        assert "input_schema" in s
        assert s["input_schema"]["type"] == "object"


def test_to_openai_format():
    r = make_registry(make_tool("alpha"))
    schemas = r.to_openai_format()
    assert len(schemas) == 1
    s = schemas[0]
    assert s["type"] == "function"
    assert "function" in s
    assert s["function"]["name"] == "alpha"
    assert "parameters" in s["function"]


# ── Execution ──────────────────────────────────────────────────────────────────

async def test_execute_calls_handler_with_params():
    handler = AsyncMock(return_value={"result": "ok"})
    tool = make_tool("my_tool", handler=handler)

    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        r = make_registry(tool)
        result = await r.execute("my_tool", {"foo": "bar"})

    handler.assert_awaited_once_with(db=mock_db, foo="bar")
    assert result == {"result": "ok"}


async def test_execute_unknown_tool_returns_error():
    r = ToolRegistry()
    result = await r.execute("nonexistent", {})
    assert "error" in result
    assert "nonexistent" in result["error"]


async def test_execute_handler_exception_returns_error():
    handler = AsyncMock(side_effect=ValueError("boom"))
    tool = make_tool("bad_tool", handler=handler)

    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        r = make_registry(tool)
        result = await r.execute("bad_tool", {})

    assert "error" in result
    assert "boom" in result["error"]
