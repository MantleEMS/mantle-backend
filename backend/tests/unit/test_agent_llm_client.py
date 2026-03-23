"""Unit tests for LLMClient — Anthropic and OpenAI/Ollama loops."""

import json
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent.llm_client import LLMClient, LLMConfig, AgentResult
from app.tools.registry import ToolRegistry, ToolDefinition


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_config(provider="anthropic", model="claude-sonnet-4-20250514"):
    return LLMConfig(
        provider=provider,
        model=model,
        api_key="sk-ant-test",
        base_url="http://localhost:11434",
        temperature=0.0,
        max_tokens=1024,
        timeout_seconds=30,
    )


def make_registry_with_tool(name="echo", return_value=None):
    handler = AsyncMock(return_value=return_value or {"result": "done"})
    tool = ToolDefinition(
        name=name,
        description="Echo tool",
        parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
        handler=handler,
        category="data",
    )
    r = ToolRegistry()
    r.register(tool)
    return r, handler


# ── Anthropic helpers ──────────────────────────────────────────────────────────

def _anthropic_text_response(text="Done."):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "end_turn"
    return resp


def _anthropic_tool_response(tool_name, tool_id, tool_input):
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = tool_name
    block.input = tool_input
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "tool_use"
    return resp


# ── AgentResult dataclass ─────────────────────────────────────────────────────

def test_agent_result_defaults():
    r = AgentResult(final_text="Hello")
    assert r.final_text == "Hello"
    assert r.trace == []
    assert r.iterations == 0
    assert r.success is True


def test_agent_result_with_trace():
    trace = [{"tool": "my_tool", "params": {}, "result": "{}"}]
    r = AgentResult(final_text="done", trace=trace, iterations=2, success=True)
    assert len(r.trace) == 1


# ── LLMConfig ─────────────────────────────────────────────────────────────────

def test_llm_config_defaults():
    c = LLMConfig(provider="ollama", model="llama3.1:8b")
    assert c.temperature == 0.0
    assert c.max_tokens == 2048
    assert c.timeout_seconds == 30
    assert c.num_ctx == 8192


# ── Anthropic: single turn (no tool calls) ────────────────────────────────────

async def test_anthropic_single_turn_returns_text():
    config = make_config(provider="anthropic")
    client = LLMClient(config)
    registry, _ = make_registry_with_tool()

    mock_anthropic = MagicMock()
    mock_anthropic_client = AsyncMock()
    mock_anthropic_client.messages.create = AsyncMock(
        return_value=_anthropic_text_response("SOP complete.")
    )
    mock_anthropic.AsyncAnthropic.return_value = mock_anthropic_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
        result = await client.run_agent(
            system_prompt="You are mantle.",
            messages=[{"role": "user", "content": "Emergency triggered."}],
            registry=registry,
            max_iterations=5,
        )

    assert result.success is True
    assert result.final_text == "SOP complete."
    assert result.iterations == 1
    assert result.trace == []


# ── Anthropic: tool call then done ────────────────────────────────────────────

async def test_anthropic_tool_call_then_done():
    config = make_config(provider="anthropic")
    client = LLMClient(config)
    registry, handler = make_registry_with_tool("echo", {"msg_echo": "hi"})

    # Patch the db session so the tool doesn't actually need a DB
    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_anthropic = MagicMock()
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[
            _anthropic_tool_response("echo", "tu_001", {"msg": "hello"}),
            _anthropic_text_response("All done."),
        ])
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = await client.run_agent(
                system_prompt="You are mantle.",
                messages=[{"role": "user", "content": "Go."}],
                registry=registry,
                max_iterations=5,
            )

    assert result.success is True
    assert result.final_text == "All done."
    assert result.iterations == 2
    assert len(result.trace) == 1
    assert result.trace[0]["tool"] == "echo"


# ── Anthropic: parallel tool calls ───────────────────────────────────────────

async def test_anthropic_parallel_tool_calls():
    config = make_config(provider="anthropic")
    client = LLMClient(config)

    # Two tools
    registry = ToolRegistry()
    for name in ["tool_a", "tool_b"]:
        registry.register(ToolDefinition(
            name=name, description="", category="data",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=AsyncMock(return_value={"from": name}),
        ))

    # Response with two tool_use blocks
    block_a = MagicMock()
    block_a.type = "tool_use"
    block_a.id = "tu_a"
    block_a.name = "tool_a"
    block_a.input = {}

    block_b = MagicMock()
    block_b.type = "tool_use"
    block_b.id = "tu_b"
    block_b.name = "tool_b"
    block_b.input = {}

    parallel_response = MagicMock()
    parallel_response.content = [block_a, block_b]
    parallel_response.stop_reason = "tool_use"

    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_anthropic = MagicMock()
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[
            parallel_response,
            _anthropic_text_response("Both done."),
        ])
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = await client.run_agent(
                system_prompt="",
                messages=[{"role": "user", "content": "Go."}],
                registry=registry,
                max_iterations=5,
            )

    assert result.success is True
    assert len(result.trace) == 2
    tool_names = {t["tool"] for t in result.trace}
    assert tool_names == {"tool_a", "tool_b"}


# ── Anthropic: max iterations ─────────────────────────────────────────────────

async def test_anthropic_max_iterations_returns_failure():
    config = make_config(provider="anthropic")
    client = LLMClient(config)
    registry, _ = make_registry_with_tool("looper")

    looping_response = _anthropic_tool_response("looper", "tu_001", {"msg": "go"})

    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_anthropic = MagicMock()
        mock_client = AsyncMock()
        # Always returns tool calls — never finishes
        mock_client.messages.create = AsyncMock(return_value=looping_response)
        mock_anthropic.AsyncAnthropic.return_value = mock_client

        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            result = await client.run_agent(
                system_prompt="",
                messages=[{"role": "user", "content": "Go."}],
                registry=registry,
                max_iterations=3,
            )

    assert result.success is False
    assert result.iterations == 3


# ── Anthropic: Bedrock routes to AnthropicBedrock ────────────────────────────

async def test_bedrock_uses_anthropic_bedrock_client():
    config = make_config(provider="bedrock", model="anthropic.claude-sonnet-4-*")
    client = LLMClient(config)
    registry, _ = make_registry_with_tool()

    mock_anthropic = MagicMock()
    mock_bedrock_client = AsyncMock()
    mock_bedrock_client.messages.create = AsyncMock(
        return_value=_anthropic_text_response("Done from Bedrock.")
    )
    mock_anthropic.AsyncAnthropicBedrock.return_value = mock_bedrock_client

    with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
        result = await client.run_agent(
            system_prompt="",
            messages=[{"role": "user", "content": "Go."}],
            registry=registry,
        )

    mock_anthropic.AsyncAnthropicBedrock.assert_called_once()
    assert result.success is True


# ── OpenAI/Ollama: single turn ────────────────────────────────────────────────

def _openai_finish_response(content="Done."):
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message = MagicMock()
    choice.message.role = "assistant"
    choice.message.content = content
    choice.message.tool_calls = None
    response = MagicMock()
    response.choices = [choice]
    return response


def _openai_tool_response(tool_name, tool_id, arguments: dict):
    tc = MagicMock()
    tc.id = tool_id
    tc.type = "function"
    tc.function = MagicMock()
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(arguments)

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message = MagicMock()
    choice.message.role = "assistant"
    choice.message.content = None
    choice.message.tool_calls = [tc]
    response = MagicMock()
    response.choices = [choice]
    return response


async def test_openai_single_turn_returns_text():
    config = make_config(provider="ollama", model="qwen2.5:14b")
    client = LLMClient(config)
    registry, _ = make_registry_with_tool()

    mock_openai = MagicMock()
    mock_oai_client = AsyncMock()
    mock_oai_client.chat.completions.create = AsyncMock(
        return_value=_openai_finish_response("SOP done via Ollama.")
    )
    mock_openai.AsyncOpenAI.return_value = mock_oai_client

    with patch.dict("sys.modules", {"openai": mock_openai}):
        result = await client.run_agent(
            system_prompt="",
            messages=[{"role": "user", "content": "Go."}],
            registry=registry,
        )

    assert result.success is True
    assert result.final_text == "SOP done via Ollama."
    assert result.trace == []


async def test_openai_tool_call_then_done():
    config = make_config(provider="ollama", model="qwen2.5:14b")
    client = LLMClient(config)
    registry, handler = make_registry_with_tool("echo", {"data": "x"})

    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_openai = MagicMock()
        mock_oai_client = AsyncMock()
        mock_oai_client.chat.completions.create = AsyncMock(side_effect=[
            _openai_tool_response("echo", "call_001", {"msg": "hello"}),
            _openai_finish_response("All done."),
        ])
        mock_openai.AsyncOpenAI.return_value = mock_oai_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = await client.run_agent(
                system_prompt="",
                messages=[{"role": "user", "content": "Go."}],
                registry=registry,
            )

    assert result.success is True
    assert len(result.trace) == 1
    assert result.trace[0]["tool"] == "echo"


# ── Provider routing ──────────────────────────────────────────────────────────

async def test_anthropic_provider_uses_anthropic_path():
    config = make_config(provider="anthropic")
    client = LLMClient(config)
    registry, _ = make_registry_with_tool()

    with patch.object(client, "_run_anthropic", new_callable=AsyncMock) as mock_a, \
         patch.object(client, "_run_openai", new_callable=AsyncMock):
        mock_a.return_value = AgentResult(final_text="ok", success=True)
        await client.run_agent("", [], registry)

    mock_a.assert_awaited_once()


async def test_ollama_provider_uses_openai_path():
    config = make_config(provider="ollama")
    client = LLMClient(config)
    registry, _ = make_registry_with_tool()

    with patch.object(client, "_run_openai", new_callable=AsyncMock) as mock_o, \
         patch.object(client, "_run_anthropic", new_callable=AsyncMock):
        mock_o.return_value = AgentResult(final_text="ok", success=True)
        await client.run_agent("", [], registry)

    mock_o.assert_awaited_once()


# ── OpenAI/Ollama: num_ctx passed in extra_body ───────────────────────────────

async def test_openai_num_ctx_passed_in_extra_body():
    config = LLMConfig(provider="ollama", model="qwen3.5", num_ctx=16384,
                       temperature=0.0, max_tokens=1024, timeout_seconds=30)
    client = LLMClient(config)
    registry, _ = make_registry_with_tool()

    mock_openai = MagicMock()
    mock_oai_client = AsyncMock()
    mock_oai_client.chat.completions.create = AsyncMock(
        return_value=_openai_finish_response("done")
    )
    mock_openai.AsyncOpenAI.return_value = mock_oai_client

    with patch.dict("sys.modules", {"openai": mock_openai}):
        await client.run_agent("", [{"role": "user", "content": "go"}], registry)

    call_kwargs = mock_oai_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["extra_body"] == {"options": {"num_ctx": 16384}}


# ── OpenAI/Ollama: parallel tool calls ───────────────────────────────────────

async def test_openai_parallel_tool_calls():
    config = make_config(provider="ollama")
    client = LLMClient(config)

    registry = ToolRegistry()
    for name in ["tool_x", "tool_y"]:
        registry.register(ToolDefinition(
            name=name, description="", category="data",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=AsyncMock(return_value={"from": name}),
        ))

    tc_x = MagicMock()
    tc_x.id = "call_x"
    tc_x.type = "function"
    tc_x.function = MagicMock(name="tool_x", arguments="{}")
    tc_x.function.name = "tool_x"
    tc_x.function.arguments = "{}"

    tc_y = MagicMock()
    tc_y.id = "call_y"
    tc_y.type = "function"
    tc_y.function = MagicMock(name="tool_y", arguments="{}")
    tc_y.function.name = "tool_y"
    tc_y.function.arguments = "{}"

    parallel_response = MagicMock()
    parallel_response.choices = [MagicMock(
        finish_reason="tool_calls",
        message=MagicMock(role="assistant", content=None, tool_calls=[tc_x, tc_y])
    )]

    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_openai = MagicMock()
        mock_oai_client = AsyncMock()
        mock_oai_client.chat.completions.create = AsyncMock(side_effect=[
            parallel_response,
            _openai_finish_response("Both done."),
        ])
        mock_openai.AsyncOpenAI.return_value = mock_oai_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = await client.run_agent("", [{"role": "user", "content": "go"}], registry)

    assert result.success is True
    tool_names = {t["tool"] for t in result.trace}
    assert tool_names == {"tool_x", "tool_y"}


# ── OpenAI/Ollama: max iterations ────────────────────────────────────────────

async def test_openai_max_iterations_returns_failure():
    config = make_config(provider="ollama")
    client = LLMClient(config)
    registry, _ = make_registry_with_tool("looper")

    looping_response = _openai_tool_response("looper", "call_001", {"msg": "loop"})

    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_openai = MagicMock()
        mock_oai_client = AsyncMock()
        mock_oai_client.chat.completions.create = AsyncMock(return_value=looping_response)
        mock_openai.AsyncOpenAI.return_value = mock_oai_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = await client.run_agent("", [{"role": "user", "content": "go"}],
                                            registry, max_iterations=3)

    assert result.success is False
    assert result.iterations == 3


# ── Tool error handling ───────────────────────────────────────────────────────

async def test_tool_error_recorded_in_trace():
    """If a tool raises, the error is captured in the trace and execution continues."""
    config = make_config(provider="ollama")
    client = LLMClient(config)

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="boom",
        description="always fails",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=AsyncMock(side_effect=RuntimeError("DB is down")),
        category="data",
    ))

    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_openai = MagicMock()
        mock_oai_client = AsyncMock()
        mock_oai_client.chat.completions.create = AsyncMock(side_effect=[
            _openai_tool_response("boom", "call_err", {}),
            _openai_finish_response("Handled error."),
        ])
        mock_openai.AsyncOpenAI.return_value = mock_oai_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = await client.run_agent("", [{"role": "user", "content": "go"}], registry)

    assert result.success is True
    assert len(result.trace) == 1
    assert "Error" in result.trace[0]["result"] or "error" in result.trace[0]["result"].lower()


async def test_unknown_tool_returns_error_in_trace():
    config = make_config(provider="ollama")
    client = LLMClient(config)
    registry = ToolRegistry()  # empty — no tools registered

    # Manually register a dummy so the call goes through but references unknown
    registry.register(ToolDefinition(
        name="known", description="", category="data",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=AsyncMock(return_value={}),
    ))

    with patch("app.tools.registry.AsyncSessionLocal") as mock_session_cls:
        mock_db = AsyncMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_openai = MagicMock()
        mock_oai_client = AsyncMock()
        mock_oai_client.chat.completions.create = AsyncMock(side_effect=[
            _openai_tool_response("nonexistent_tool", "call_unk", {}),
            _openai_finish_response("Done."),
        ])
        mock_openai.AsyncOpenAI.return_value = mock_oai_client

        with patch.dict("sys.modules", {"openai": mock_openai}):
            result = await client.run_agent("", [{"role": "user", "content": "go"}], registry)

    assert len(result.trace) == 1
    assert "Unknown tool" in result.trace[0]["result"] or "error" in result.trace[0]["result"].lower()
