"""
LLMClient — unified interface for all LLM providers.
Abstracts Ollama (OpenAI-compatible), Anthropic, and Bedrock behind one async interface.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class LLMConfig:
    provider: str                       # ollama | anthropic | bedrock
    model: str
    base_url: str = "http://localhost:11434"  # Ollama only
    api_key: str | None = None          # Anthropic only
    aws_region: str = "us-east-1"       # Bedrock only
    temperature: float = 0.0
    max_tokens: int = 2048
    timeout_seconds: int = 30
    num_ctx: int = 8192                 # Ollama only — context window size


@dataclass
class AgentResult:
    final_text: str
    trace: list[dict] = field(default_factory=list)  # [{tool, params, result}, ...]
    iterations: int = 0
    success: bool = True
    conversation: list[dict] = field(default_factory=list)  # full message history in OpenAI format


def _normalize_anthropic_conversation(system_prompt: str, msgs: list[dict]) -> list[dict]:
    """
    Convert Anthropic-format messages to OpenAI format for uniform training data storage.
    Anthropic assistant content is a list of content blocks; OpenAI uses plain strings + tool_calls.
    """
    result = [{"role": "system", "content": system_prompt}]
    for msg in msgs:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            # Could be a plain string or a list of tool_result blocks
            if isinstance(content, str):
                result.append({"role": "user", "content": content})
            else:
                # List of tool_result blocks — emit one tool message per result
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        result.append({
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"],
                        })

        elif role == "assistant":
            if isinstance(content, str):
                result.append({"role": "assistant", "content": content})
            else:
                # List of content blocks (text + tool_use)
                text = " ".join(b.text for b in content if hasattr(b, "type") and b.type == "text")
                tool_calls = [
                    {
                        "id": b.id,
                        "type": "function",
                        "function": {"name": b.name, "arguments": json.dumps(b.input)},
                    }
                    for b in content
                    if hasattr(b, "type") and b.type == "tool_use"
                ]
                entry: dict = {"role": "assistant", "content": text or None}
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                result.append(entry)

    return result


class LLMClient:
    """Unified LLM provider abstraction. All agent code calls this — never the SDKs directly."""

    def __init__(self, config: LLMConfig):
        self.config = config

    async def run_agent(
        self,
        system_prompt: str,
        messages: list[dict],
        registry: ToolRegistry,
        max_iterations: int = 15,
    ) -> AgentResult:
        """
        Execute the agent loop:
        1. Send prompt + messages + tools to LLM
        2. LLM returns text and/or tool calls
        3. Execute tool calls (in parallel when possible), append results
        4. Repeat until LLM returns text with no tool calls or max_iterations reached
        """
        if self.config.provider in ("anthropic", "bedrock"):
            return await self._run_anthropic(system_prompt, messages, registry, max_iterations)
        else:
            return await self._run_openai(system_prompt, messages, registry, max_iterations)

    # ── Anthropic / Bedrock ────────────────────────────────────────────────────

    async def _run_anthropic(
        self,
        system_prompt: str,
        messages: list[dict],
        registry: ToolRegistry,
        max_iterations: int,
    ) -> AgentResult:
        try:
            import anthropic as anthropic_sdk
        except ImportError:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")

        if self.config.provider == "bedrock":
            client = anthropic_sdk.AsyncAnthropicBedrock(aws_region=self.config.aws_region)
        else:
            client = anthropic_sdk.AsyncAnthropic(api_key=self.config.api_key)

        # Cache the static parts (system prompt + tool schemas) — charged at 25% on first
        # write, then 10% on reads. Saves ~60% of input tokens on multi-iteration runs.
        tool_schemas = registry.to_anthropic_format()
        if tool_schemas:
            tool_schemas[-1] = {**tool_schemas[-1], "cache_control": {"type": "ephemeral"}}
        cached_system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]

        msgs = list(messages)  # copy — we mutate this
        trace: list[dict] = []

        for iteration in range(max_iterations):
            t_call = time.monotonic()
            response = await asyncio.wait_for(
                client.messages.create(
                    model=self.config.model,
                    system=cached_system,
                    messages=msgs,
                    tools=tool_schemas,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                ),
                timeout=self.config.timeout_seconds,
            )
            call_ms = int((time.monotonic() - t_call) * 1000)

            tool_calls = [b for b in response.content if b.type == "tool_use"]
            logger.info(
                f"llm_client iter={iteration+1} provider=anthropic "
                f"call_ms={call_ms} tool_calls={[tc.name for tc in tool_calls] or 'none'}"
            )

            if not tool_calls:
                final_text = " ".join(
                    b.text for b in response.content if b.type == "text"
                )
                logger.info(f"llm_client done iter={iteration+1} output={final_text[:200]!r}")
                # Normalize Anthropic messages to OpenAI format for training data
                conversation = _normalize_anthropic_conversation(cached_system[0]["text"], msgs)
                return AgentResult(
                    final_text=final_text, trace=trace,
                    iterations=iteration + 1, success=True,
                    conversation=conversation,
                )

            msgs.append({"role": "assistant", "content": response.content})

            results = await asyncio.gather(
                *[registry.execute(tc.name, tc.input) for tc in tool_calls],
                return_exceptions=True,
            )

            tool_results = []
            for tc, result in zip(tool_calls, results):
                content = (
                    f"Error: {result}" if isinstance(result, Exception)
                    else json.dumps(result)
                )
                logger.info(f"llm_client tool={tc.name} params={tc.input} result={content[:200]!r}")
                trace.append({"tool": tc.name, "params": tc.input, "result": content})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": content,
                })

            msgs.append({"role": "user", "content": tool_results})

        logger.warning(f"llm_client max_iterations={max_iterations} reached provider=anthropic")
        conversation = _normalize_anthropic_conversation(cached_system[0]["text"], msgs)
        return AgentResult(final_text="", trace=trace, iterations=max_iterations, success=False,
                           conversation=conversation)

    # ── Ollama / OpenAI-compatible ─────────────────────────────────────────────

    async def _run_openai(
        self,
        system_prompt: str,
        messages: list[dict],
        registry: ToolRegistry,
        max_iterations: int,
    ) -> AgentResult:
        try:
            import openai as openai_sdk
        except ImportError:
            raise RuntimeError("openai package not installed. Run: pip install openai")

        client = openai_sdk.AsyncOpenAI(
            base_url=f"{self.config.base_url.rstrip('/')}/v1",
            api_key="ollama",  # required by SDK but ignored by Ollama
        )

        tool_schemas = registry.to_openai_format()
        oai_msgs = [{"role": "system", "content": system_prompt}] + list(messages)
        trace: list[dict] = []

        for iteration in range(max_iterations):
            t_call = time.monotonic()
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=self.config.model,
                    messages=oai_msgs,
                    tools=tool_schemas,
                    tool_choice="auto",
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    extra_body={"options": {"num_ctx": self.config.num_ctx}},
                ),
                timeout=self.config.timeout_seconds,
            )
            call_ms = int((time.monotonic() - t_call) * 1000)

            choice = response.choices[0]
            tool_names = [tc.function.name for tc in (choice.message.tool_calls or [])]
            logger.info(
                f"llm_client iter={iteration+1} provider=ollama finish={choice.finish_reason} "
                f"call_ms={call_ms} tool_calls={tool_names or 'none'}"
            )

            if choice.finish_reason != "tool_calls":
                final_text = choice.message.content or ""
                logger.info(f"llm_client done iter={iteration+1} output={final_text[:200]!r}")
                oai_msgs.append({"role": "assistant", "content": final_text})
                return AgentResult(
                    final_text=final_text,
                    trace=trace,
                    iterations=iteration + 1,
                    success=True,
                    conversation=list(oai_msgs),
                )

            msg = choice.message
            oai_msgs.append({
                "role": msg.role,
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in (msg.tool_calls or [])
                ],
            })

            tool_calls = choice.message.tool_calls
            parsed = [(tc, json.loads(tc.function.arguments)) for tc in tool_calls]

            results = await asyncio.gather(
                *[registry.execute(tc.function.name, args) for tc, args in parsed],
                return_exceptions=True,
            )

            for (tc, args), result in zip(parsed, results):
                content = (
                    f"Error: {result}" if isinstance(result, Exception)
                    else json.dumps(result)
                )
                logger.info(f"llm_client tool={tc.function.name} params={args} result={content[:200]!r}")
                trace.append({"tool": tc.function.name, "params": args, "result": content})
                oai_msgs.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })

        logger.warning(f"llm_client max_iterations={max_iterations} reached provider=ollama")
        return AgentResult(final_text="", trace=trace, iterations=max_iterations, success=False,
                           conversation=list(oai_msgs))
