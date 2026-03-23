"""
Adaptive SOP tools — only registered when LLM_ADAPTIVE_SOP=true.

These tools allow the LLM to propose deviations from the pre-authored SOP
based on live context (patient conditions, facility risk, incident history).
All proposals create amber pending actions — the commander must approve them.
Nothing auto-applies.
"""

import logging
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models import Incident, SOP
from app.services.thread_service import create_message, write_audit
from app.services.action_service import create_action
from app.tools.registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)


async def propose_step_adaptation(
    db: AsyncSession,
    incident_id: str,
    step_number: int,
    adaptation_type: str,
    reason: str,
    proposed_description: str,
) -> dict:
    """
    Propose a modification to a specific SOP step based on incident context.
    Creates an amber pending action — commander must approve before any change takes effect.

    adaptation_type: "modify" | "skip" | "add_after"
    """
    result = await db.execute(select(Incident).where(Incident.id == UUID(incident_id)))
    incident = result.scalar_one_or_none()
    if not incident:
        return {"error": "Incident not found"}

    description = (
        f"[Adaptive SOP] Step {step_number} — {adaptation_type.upper()}\n"
        f"Reason: {reason}\n"
        f"Proposed: {proposed_description}"
    )

    action = await create_action(
        db=db,
        incident_id=UUID(incident_id),
        org_id=incident.org_id,
        action_type="sop_adaptation",
        description=description,
        tier="amber",
    )

    await create_message(
        db=db,
        incident_id=UUID(incident_id),
        sender_type="ai",
        message_type="classification",
        content=(
            f"SOP adaptation proposed for step {step_number} ({adaptation_type}): {reason}. "
            f"Commander approval required."
        ),
        metadata={
            "event": "sop.adaptation_proposed",
            "step_number": step_number,
            "adaptation_type": adaptation_type,
            "reason": reason,
            "proposed_description": proposed_description,
            "action_id": str(action.id),
        },
    )

    await write_audit(
        db=db,
        org_id=incident.org_id,
        event_type="sop.adaptation_proposed",
        actor_type="ai",
        incident_id=UUID(incident_id),
        detail={
            "step_number": step_number,
            "adaptation_type": adaptation_type,
            "reason": reason,
            "proposed_description": proposed_description,
        },
    )

    logger.info(
        f"[{incident_id}] adaptive: step={step_number} type={adaptation_type} reason={reason!r}"
    )

    return {
        "status": "proposed",
        "action_id": str(action.id),
        "step_number": step_number,
        "adaptation_type": adaptation_type,
        "awaiting_commander_approval": True,
    }


async def propose_sop_switch(
    db: AsyncSession,
    incident_id: str,
    current_sop_code: str,
    recommended_sop_code: str,
    reason: str,
) -> dict:
    """
    Propose switching to a different SOP when the triggered one doesn't match
    the actual situation (e.g. medical SOP triggered but situation is workplace violence).
    Creates an amber pending action — commander must approve.
    """
    result = await db.execute(select(Incident).where(Incident.id == UUID(incident_id)))
    incident = result.scalar_one_or_none()
    if not incident:
        return {"error": "Incident not found"}

    # Verify the recommended SOP exists
    sop_result = await db.execute(
        select(SOP).where(
            and_(
                SOP.org_id == incident.org_id,
                SOP.sop_code == recommended_sop_code,
                SOP.is_active == True,
            )
        )
    )
    recommended_sop = sop_result.scalar_one_or_none()
    recommended_name = recommended_sop.name if recommended_sop else recommended_sop_code

    description = (
        f"[Adaptive SOP] Switch SOP: {current_sop_code} → {recommended_sop_code}\n"
        f"Reason: {reason}\n"
        f"Recommended SOP: {recommended_name}"
    )

    action = await create_action(
        db=db,
        incident_id=UUID(incident_id),
        org_id=incident.org_id,
        action_type="sop_switch",
        description=description,
        tier="amber",
    )

    await create_message(
        db=db,
        incident_id=UUID(incident_id),
        sender_type="ai",
        message_type="classification",
        content=(
            f"SOP mismatch detected. Recommending switch from {current_sop_code} "
            f"to {recommended_sop_code}: {reason}. Commander approval required."
        ),
        metadata={
            "event": "sop.switch_proposed",
            "current_sop_code": current_sop_code,
            "recommended_sop_code": recommended_sop_code,
            "recommended_sop_name": recommended_name,
            "reason": reason,
            "action_id": str(action.id),
        },
    )

    await write_audit(
        db=db,
        org_id=incident.org_id,
        event_type="sop.switch_proposed",
        actor_type="ai",
        incident_id=UUID(incident_id),
        detail={
            "current_sop_code": current_sop_code,
            "recommended_sop_code": recommended_sop_code,
            "reason": reason,
        },
    )

    logger.info(
        f"[{incident_id}] adaptive: sop_switch {current_sop_code}→{recommended_sop_code} "
        f"reason={reason!r}"
    )

    return {
        "status": "proposed",
        "action_id": str(action.id),
        "current_sop_code": current_sop_code,
        "recommended_sop_code": recommended_sop_code,
        "recommended_sop_name": recommended_name,
        "awaiting_commander_approval": True,
    }


# ── Registration ───────────────────────────────────────────────────────────────

def register_adaptive_tools(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="propose_step_adaptation",
        description=(
            "Propose a modification to a specific SOP step when live context warrants it "
            "(e.g. patient is DNR — skip CPR step; poor cell coverage — escalate 911 priority). "
            "Creates an amber pending action for commander approval. Never auto-applies."
        ),
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "step_number": {"type": "integer", "description": "The SOP step number to adapt"},
                "adaptation_type": {
                    "type": "string",
                    "enum": ["modify", "skip", "add_after"],
                    "description": "Type of adaptation: modify (change description/tier), skip (omit step), add_after (insert new step)",
                },
                "reason": {
                    "type": "string",
                    "description": "Specific contextual reason — cite the patient condition, risk flag, or data point that justifies this",
                },
                "proposed_description": {
                    "type": "string",
                    "description": "The proposed step text or the new step to add after",
                },
            },
            "required": ["incident_id", "step_number", "adaptation_type", "reason", "proposed_description"],
        },
        handler=propose_step_adaptation,
        category="adaptive",
    ))

    registry.register(ToolDefinition(
        name="propose_sop_switch",
        description=(
            "Propose switching to a different SOP when the one triggered doesn't match the situation. "
            "Example: a medical SOP was auto-triggered but context reveals workplace violence. "
            "Creates an amber pending action for commander approval. "
            "Call get_sop first to confirm the recommended SOP exists."
        ),
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "current_sop_code": {"type": "string", "description": "SOP code currently in use"},
                "recommended_sop_code": {"type": "string", "description": "SOP code you recommend switching to"},
                "reason": {
                    "type": "string",
                    "description": "Specific contextual reason — cite the evidence that indicates the wrong SOP was triggered",
                },
            },
            "required": ["incident_id", "current_sop_code", "recommended_sop_code", "reason"],
        },
        handler=propose_sop_switch,
        category="adaptive",
    ))
