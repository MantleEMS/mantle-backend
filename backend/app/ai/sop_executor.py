"""
Scripted AI SOP executor for demo purposes.
Reads SOP steps from DB and executes them with fixed timing.
NOT LLM-driven — deterministic and reliable for demo reproducibility.
"""

import asyncio
import logging
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models import SOP, Incident, User
from app.services.thread_service import create_message, write_audit
from app.services.action_service import create_action
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

# Delay between SOP steps in seconds (demo timing)
STEP_DELAY_SECONDS = 2


async def execute_sop_for_incident(incident_id: UUID, org_id: UUID, sop_id: UUID):
    """
    Execute SOP steps as a background task with fixed timing.
    Each step runs STEP_DELAY_SECONDS after the previous.
    """
    logger.info(f"Starting SOP execution for incident {incident_id}")

    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(select(SOP).where(SOP.id == sop_id))
            sop = result.scalar_one_or_none()
            if not sop:
                logger.warning(f"SOP {sop_id} not found")
                return

            steps = sop.steps or []
            logger.info(f"Executing {len(steps)} SOP steps for incident {incident_id}")

            for step_data in steps:
                await asyncio.sleep(STEP_DELAY_SECONDS)

                step_num = step_data.get("step", 0)
                actor = step_data.get("actor", "ai")
                action_type = step_data.get("action", "")
                auto = step_data.get("auto", False)
                description = step_data.get("description", "")
                tier = step_data.get("tier", "green")

                # Re-check incident is still active
                inc_result = await db.execute(
                    select(Incident).where(Incident.id == incident_id)
                )
                incident = inc_result.scalar_one_or_none()
                if not incident or incident.status in ("resolved", "cancelled"):
                    logger.info(f"Incident {incident_id} is no longer active, stopping SOP")
                    break

                # Update incident to active after first step
                if step_num == 1 and incident.status == "triggered":
                    incident.status = "active"
                    await db.commit()

                if actor == "ai" and auto:
                    # Auto-execute: AI performs action directly
                    await create_message(
                        db=db,
                        incident_id=incident_id,
                        sender_type="ai",
                        message_type="action",
                        content=f"AI executing step {step_num}: {description}",
                        metadata={
                            "sop_step": step_num,
                            "action_type": action_type,
                            "auto": True,
                            "tier": tier,
                        },
                    )
                    await write_audit(
                        db=db,
                        org_id=org_id,
                        event_type="sop.step_executed",
                        actor_type="ai",
                        incident_id=incident_id,
                        detail={"step": step_num, "action_type": action_type, "description": description},
                    )

                    # Special handling for specific action types
                    if action_type == "begin_recording":
                        await _handle_begin_recording(db, incident_id, org_id, step_num)
                    elif action_type == "alert_commander":
                        await _handle_alert_commander(db, incident, step_num)

                else:
                    # Requires commander decision: create pending action
                    await create_action(
                        db=db,
                        incident_id=incident_id,
                        org_id=org_id,
                        action_type=action_type,
                        description=description,
                        tier=tier,
                        sop_step=step_num,
                    )
                    await create_message(
                        db=db,
                        incident_id=incident_id,
                        sender_type="ai",
                        message_type="classification",
                        content=f"Recommending action (Step {step_num}): {description}",
                        metadata={
                            "sop_step": step_num,
                            "action_type": action_type,
                            "tier": tier,
                            "requires_approval": True,
                        },
                    )

        except asyncio.CancelledError:
            logger.info(f"SOP execution cancelled for incident {incident_id}")
        except Exception as e:
            logger.error(f"SOP execution error for incident {incident_id}: {e}", exc_info=True)


async def _handle_begin_recording(db: AsyncSession, incident_id: UUID, org_id: UUID, step: int):
    await create_message(
        db=db,
        incident_id=incident_id,
        sender_type="ai",
        message_type="system_event",
        content="Audio/video recording started automatically.",
        metadata={"event": "recording.started", "sop_step": step},
    )


async def _handle_alert_commander(db: AsyncSession, incident: Incident, step: int):
    if incident.commander_id:
        from app.notifications.push import send_push_notification
        await send_push_notification(
            db=db,
            user_id=incident.commander_id,
            title=f"SOS: {incident.emergency_type.replace('_', ' ').title()}",
            body=f"Incident {incident.incident_number} requires your attention.",
            data={"deep_link": f"mantle://incident/{incident.id}/commander"},
        )
