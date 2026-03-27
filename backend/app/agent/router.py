"""
AgentRouter — routes incidents to scripted or LLM agent based on AI_MODE setting.
Automatically falls back to scripted if the LLM times out or errors.
"""

import asyncio
import logging
import time
from uuid import UUID

from app.config import settings
from app.agent.scripted import ScriptedAgent
from app.metrics import agent_runs, agent_duration

logger = logging.getLogger(__name__)


class AgentRouter:
    def __init__(self):
        self.mode = settings.AI_MODE
        self.scripted = ScriptedAgent()
        self.llm_agent = None
        self.thread_agent = None

        if self.mode == "llm":
            self._init_llm_agent()

    def _init_llm_agent(self):
        try:
            from app.agent.llm_client import LLMClient, LLMConfig
            from app.agent.llm_agent import LLMAgent
            from app.agent.thread_agent import ThreadAgent
            from app.tools.registry import build_registry

            config = LLMConfig(
                provider=settings.LLM_PROVIDER,
                model=settings.LLM_MODEL,
                base_url=settings.LLM_BASE_URL,
                api_key=settings.ANTHROPIC_API_KEY,
                aws_region=settings.AWS_REGION,
                temperature=settings.LLM_TEMPERATURE,
                max_tokens=settings.LLM_MAX_TOKENS,
                timeout_seconds=settings.LLM_TIMEOUT,
                num_ctx=settings.LLM_NUM_CTX,
            )
            registry = build_registry()
            client = LLMClient(config)
            self.llm_agent = LLMAgent(client, registry)
            self.thread_agent = ThreadAgent(client, registry)
            logger.info(
                f"LLM agent ready: provider={settings.LLM_PROVIDER} model={settings.LLM_MODEL}"
            )
            logger.info("Thread agent ready")
        except Exception as e:
            logger.error(f"Failed to initialize LLM agent: {e}. Falling back to scripted mode.")
            self.mode = "scripted"

    async def handle_incident(self, incident_id: UUID, org_id: UUID, sop_id: UUID):
        """Dispatch incident to the appropriate agent."""
        t0 = time.monotonic()

        if self.mode == "scripted" or self.llm_agent is None:
            logger.info(f"[{incident_id}] agent=scripted reason=mode:{self.mode}")
            await self.scripted.execute(incident_id, org_id, sop_id)
            elapsed = time.monotonic() - t0
            agent_runs.labels(agent_type="scripted", status="success").inc()
            agent_duration.labels(agent_type="scripted").observe(elapsed)
            logger.info(f"[{incident_id}] scripted finished elapsed={elapsed:.1f}s")
            return

        try:
            await asyncio.wait_for(
                self.llm_agent.execute(incident_id, org_id, sop_id),
                timeout=settings.LLM_TIMEOUT + 5,
            )
            elapsed = time.monotonic() - t0
            agent_runs.labels(agent_type="llm", status="success").inc()
            agent_duration.labels(agent_type="llm").observe(elapsed)
            logger.info(
                f"[{incident_id}] agent=llm model={settings.LLM_MODEL} "
                f"elapsed={elapsed:.1f}s status=success"
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            agent_runs.labels(agent_type="llm", status="timeout").inc()
            agent_duration.labels(agent_type="llm").observe(elapsed)
            logger.warning(
                f"[{incident_id}] agent=llm status=timeout elapsed={elapsed:.1f}s "
                f"fallback=scripted"
            )
            await self.scripted.execute(incident_id, org_id, sop_id)
        except Exception as e:
            elapsed = time.monotonic() - t0
            agent_runs.labels(agent_type="llm", status="error").inc()
            agent_duration.labels(agent_type="llm").observe(elapsed)
            logger.warning(
                f"[{incident_id}] agent=llm status=error elapsed={elapsed:.1f}s "
                f"fallback=scripted error={e!r}"
            )
            await self.scripted.execute(incident_id, org_id, sop_id)


# Singleton — instantiated once at import time, reads from settings
agent_router = AgentRouter()
