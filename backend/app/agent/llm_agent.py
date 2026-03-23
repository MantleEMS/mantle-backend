"""
LLMAgent — SOP execution via tool-calling LLM.
Loads incident context, builds the system prompt, and runs the agent loop.
"""

import json
import logging
from uuid import UUID
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Incident, SOP, TrainingSample
from app.agent.llm_client import LLMClient, AgentResult
from app.agent.system_prompt import build_system_prompt
from app.services.thread_service import write_audit
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class LLMAgent:
    def __init__(self, llm: LLMClient, registry: ToolRegistry):
        self.llm = llm
        self.registry = registry

    async def execute(self, incident_id: UUID, org_id: UUID, sop_id: UUID) -> AgentResult:
        async with AsyncSessionLocal() as db:
            # Load incident
            inc_result = await db.execute(select(Incident).where(Incident.id == incident_id))
            incident = inc_result.scalar_one_or_none()
            if not incident:
                raise ValueError(f"Incident {incident_id} not found")

            # Mark active
            if incident.status == "triggered":
                incident.status = "active"
                await db.commit()

            # Load SOP
            sop_result = await db.execute(select(SOP).where(SOP.id == sop_id))
            sop = sop_result.scalar_one_or_none()
            if not sop:
                raise ValueError(f"SOP {sop_id} not found")

            sop_dict = {
                "id": str(sop.id),
                "name": sop.name,
                "emergency_type": sop.emergency_type,
                "steps": sop.steps or [],
                "responder_checklist": sop.responder_checklist or [],
            }

        system_prompt = build_system_prompt(sop_dict, adaptive=settings.LLM_ADAPTIVE_SOP)

        user_content = (
            f"An emergency has been triggered.\n"
            f"Incident ID: {incident_id}\n"
            f"Organization ID: {org_id}\n"
            f"Emergency type: {incident.emergency_type}\n"
            f"Trigger source: {incident.trigger_source}\n"
            f"Facility ID: {incident.facility_id or 'unknown'}\n"
            f"Commander ID: {incident.commander_id or 'none assigned'}\n\n"
            f"Execute the SOP. Use tools to gather data and take actions. "
            f"Present red-tier actions as pending decisions for the commander."
        )
        messages = [{"role": "user", "content": user_content}]

        logger.info(
            f"[{incident_id}] LLM agent starting "
            f"emergency={incident.emergency_type} sop={sop_dict['name']!r} "
            f"model={self.llm.config.model} provider={self.llm.config.provider}"
        )
        logger.debug(f"[{incident_id}] system_prompt={system_prompt[:500]!r}")
        logger.debug(f"[{incident_id}] user_message={user_content!r}")

        result = await self.llm.run_agent(
            system_prompt=system_prompt,
            messages=messages,
            registry=self.registry,
            max_iterations=self.llm.config.max_tokens and 15,
        )

        await self._log_trace(incident_id, org_id, result)

        logger.info(
            f"[{incident_id}] LLM agent finished "
            f"success={result.success} iterations={result.iterations} "
            f"tool_calls={len(result.trace)} output={result.final_text[:200]!r}"
        )
        return result

    async def _log_trace(self, incident_id: UUID, org_id: UUID, result: AgentResult):
        async with AsyncSessionLocal() as db:
            await write_audit(
                db=db,
                org_id=org_id,
                event_type="sop.llm_agent_completed",
                actor_type="ai",
                incident_id=incident_id,
                detail={
                    "success": result.success,
                    "iterations": result.iterations,
                    "tool_calls": [
                        {"tool": t["tool"], "params": t["params"]}
                        for t in result.trace
                    ],
                },
            )

            if result.conversation:
                # Load incident for metadata
                inc = await db.get(Incident, incident_id)
                db.add(TrainingSample(
                    incident_id=incident_id,
                    org_id=org_id,
                    provider=self.llm.config.provider,
                    model=self.llm.config.model,
                    emergency_type=inc.emergency_type if inc else None,
                    success=result.success,
                    iterations=result.iterations,
                    conversation=result.conversation,
                ))
                await db.commit()
                logger.info(f"[{incident_id}] Training sample saved ({len(result.conversation)} messages)")
