"""
Central tool registry. Tools register once and are available to all providers.
The registry converts tool definitions to provider-specific schemas (Anthropic / OpenAI).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict       # JSON Schema for LLM-visible parameters
    handler: Callable      # async function(db, **params) -> dict
    category: str          # "data" | "action" | "external"
    llm_visible: bool = True  # False = approval-flow only, excluded from LLM schema


class ToolRegistry:
    """Central registry. Tools register once, available to all providers."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def get_all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    async def execute(self, name: str, params: dict) -> dict:
        """Execute a tool by name. Opens its own DB session."""
        tool = self._tools.get(name)
        if not tool:
            return {"error": f"Unknown tool: {name}"}
        try:
            async with AsyncSessionLocal() as db:
                return await tool.handler(db=db, **params)
        except Exception as e:
            logger.error(f"Tool {name} failed: {e}", exc_info=True)
            return {"error": str(e)}

    def to_anthropic_format(self) -> list[dict]:
        """Convert to Anthropic tool_use schema. Excludes approval-flow-only tools."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in self._tools.values()
            if t.llm_visible
        ]

    def to_openai_format(self) -> list[dict]:
        """Convert to OpenAI function calling schema (Ollama-compatible). Excludes approval-flow-only tools."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
            if t.llm_visible
        ]


def build_registry() -> ToolRegistry:
    """Create and populate the tool registry with all data and action tools."""
    from app.config import settings
    from app.tools.data_tools import register_data_tools
    from app.tools.action_tools import register_action_tools

    registry = ToolRegistry()
    register_data_tools(registry)
    register_action_tools(registry)

    if settings.LLM_ADAPTIVE_SOP:
        from app.tools.adaptive_tools import register_adaptive_tools
        register_adaptive_tools(registry)
        logger.info("Adaptive SOP tools registered (LLM_ADAPTIVE_SOP=true)")

    return registry
