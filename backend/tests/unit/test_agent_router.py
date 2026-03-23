"""Unit tests for AgentRouter — dispatching and fallback logic."""

import uuid
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


INC_ID = uuid.uuid4()
ORG_ID = uuid.uuid4()
SOP_ID = uuid.uuid4()


# ── Scripted mode ─────────────────────────────────────────────────────────────

async def test_scripted_mode_calls_scripted_agent():
    with patch("app.agent.router.settings") as mock_settings:
        mock_settings.AI_MODE = "scripted"

        from app.agent.router import AgentRouter
        router = AgentRouter()
        router.scripted.execute = AsyncMock()

        await router.handle_incident(INC_ID, ORG_ID, SOP_ID)

    router.scripted.execute.assert_awaited_once_with(INC_ID, ORG_ID, SOP_ID)


async def test_scripted_mode_never_calls_llm_agent():
    with patch("app.agent.router.settings") as mock_settings:
        mock_settings.AI_MODE = "scripted"

        from app.agent.router import AgentRouter
        router = AgentRouter()
        router.scripted.execute = AsyncMock()
        router.llm_agent = MagicMock()
        router.llm_agent.execute = AsyncMock()

        await router.handle_incident(INC_ID, ORG_ID, SOP_ID)

    router.llm_agent.execute.assert_not_awaited()


# ── LLM mode ──────────────────────────────────────────────────────────────────

async def test_llm_mode_calls_llm_agent():
    with patch("app.agent.router.settings") as mock_settings:
        mock_settings.AI_MODE = "llm"
        mock_settings.LLM_TIMEOUT = 30

        from app.agent.router import AgentRouter
        from app.agent.llm_client import AgentResult
        router = AgentRouter.__new__(AgentRouter)
        router.mode = "llm"
        router.scripted = MagicMock()
        router.scripted.execute = AsyncMock()
        router.llm_agent = MagicMock()
        router.llm_agent.execute = AsyncMock(
            return_value=AgentResult(final_text="done", success=True)
        )

        with patch("app.agent.router.settings", mock_settings):
            await router.handle_incident(INC_ID, ORG_ID, SOP_ID)

    router.llm_agent.execute.assert_awaited_once_with(INC_ID, ORG_ID, SOP_ID)
    router.scripted.execute.assert_not_awaited()


async def test_llm_mode_falls_back_on_timeout():
    with patch("app.agent.router.settings") as mock_settings:
        mock_settings.AI_MODE = "llm"
        mock_settings.LLM_TIMEOUT = 1

        from app.agent.router import AgentRouter
        router = AgentRouter.__new__(AgentRouter)
        router.mode = "llm"
        router.scripted = MagicMock()
        router.scripted.execute = AsyncMock()
        router.llm_agent = MagicMock()

        async def slow_execute(*args):
            await asyncio.sleep(100)

        router.llm_agent.execute = slow_execute

        with patch("app.agent.router.settings", mock_settings):
            await router.handle_incident(INC_ID, ORG_ID, SOP_ID)

    # Should have fallen back to scripted
    router.scripted.execute.assert_awaited_once_with(INC_ID, ORG_ID, SOP_ID)


async def test_llm_mode_falls_back_on_exception():
    with patch("app.agent.router.settings") as mock_settings:
        mock_settings.AI_MODE = "llm"
        mock_settings.LLM_TIMEOUT = 30

        from app.agent.router import AgentRouter
        router = AgentRouter.__new__(AgentRouter)
        router.mode = "llm"
        router.scripted = MagicMock()
        router.scripted.execute = AsyncMock()
        router.llm_agent = MagicMock()
        router.llm_agent.execute = AsyncMock(side_effect=RuntimeError("LLM API error"))

        with patch("app.agent.router.settings", mock_settings):
            await router.handle_incident(INC_ID, ORG_ID, SOP_ID)

    router.scripted.execute.assert_awaited_once_with(INC_ID, ORG_ID, SOP_ID)


async def test_llm_mode_no_agent_falls_back_to_scripted():
    """If llm_agent is None (init failed), falls back to scripted."""
    from app.agent.router import AgentRouter
    router = AgentRouter.__new__(AgentRouter)
    router.mode = "llm"
    router.scripted = MagicMock()
    router.scripted.execute = AsyncMock()
    router.llm_agent = None  # init failed

    with patch("app.agent.router.settings") as s:
        s.LLM_TIMEOUT = 30
        await router.handle_incident(INC_ID, ORG_ID, SOP_ID)

    router.scripted.execute.assert_awaited_once()
