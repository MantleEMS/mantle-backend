import asyncio
import logging
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func

from app.models import Incident, Participant, Action, Message, SOP, User, Facility
from app.services.thread_service import create_message, write_audit
from app.notifications.push import send_push_to_commanders

logger = logging.getLogger(__name__)


async def generate_incident_number(db: AsyncSession) -> str:
    year = datetime.now(timezone.utc).year
    result = await db.execute(
        select(func.count(Incident.id)).where(
            func.extract("year", Incident.initiated_at) == year
        )
    )
    count = result.scalar() or 0
    return f"INC-{year}-{count + 1:04d}"


async def get_sop_for_emergency(db: AsyncSession, org_id: UUID, emergency_type: str) -> SOP | None:
    result = await db.execute(
        select(SOP).where(
            and_(
                SOP.org_id == org_id,
                SOP.emergency_type == emergency_type,
                SOP.is_active == True,
            )
        ).limit(1)
    )
    return result.scalar_one_or_none()


async def create_incident(
    db: AsyncSession,
    org_id: UUID,
    initiated_by: UUID,
    emergency_type: str,
    trigger_source: str,
    facility_id: UUID = None,
    location: dict = None,
    patient_info: dict = None,
) -> Incident:
    incident_number = await generate_incident_number(db)

    # Find SOP
    sop = await get_sop_for_emergency(db, org_id, emergency_type)
    if not sop:
        logger.warning(f"No active SOP found for emergency_type='{emergency_type}' org={org_id}")

    # Find commander for org
    commander_result = await db.execute(
        select(User).where(
            and_(User.org_id == org_id, User.role == "commander", User.status != "inactive")
        ).limit(1)
    )
    commander = commander_result.scalar_one_or_none()

    incident = Incident(
        org_id=org_id,
        incident_number=incident_number,
        status="triggered",
        emergency_type=emergency_type,
        trigger_source=trigger_source,
        severity=3,
        facility_id=facility_id,
        sop_id=sop.id if sop else None,
        commander_id=commander.id if commander else None,
        initiated_by=initiated_by,
        location=location or {},
        patient_info=patient_info or {},
        ai_assessment={},
    )
    db.add(incident)
    await db.flush()  # Get the ID

    # Add initiator as participant
    initiator_result = await db.execute(select(User).where(User.id == initiated_by))
    initiator = initiator_result.scalar_one_or_none()

    participant = Participant(
        incident_id=incident.id,
        user_id=initiated_by,
        role="initiator",
        name=initiator.name if initiator else "Unknown",
        is_ai=False,
    )
    db.add(participant)

    # Add commander as participant
    if commander:
        cmd_participant = Participant(
            incident_id=incident.id,
            user_id=commander.id,
            role="commander",
            name=commander.name,
            is_ai=False,
        )
        db.add(cmd_participant)

    # Add AI agent participant
    ai_participant = Participant(
        incident_id=incident.id,
        user_id=None,
        role="ai_agent",
        name="Mantle AI",
        is_ai=True,
    )
    db.add(ai_participant)

    await db.commit()
    await db.refresh(incident)

    logger.info(
        f"Incident {incident_number} created: id={incident.id}, type={emergency_type}, "
        f"org={org_id}, commander={'assigned' if commander else 'none'}, sop={'assigned' if sop else 'none'}"
    )

    # Post system event message
    await create_message(
        db=db,
        incident_id=incident.id,
        sender_type="system",
        message_type="system_event",
        content=f"Incident {incident_number} triggered. Emergency type: {emergency_type}.",
        metadata={"event": "incident.created", "trigger_source": trigger_source},
    )

    # Write audit log
    await write_audit(
        db=db,
        org_id=org_id,
        event_type="incident.created",
        actor_type="human",
        actor_id=initiated_by,
        incident_id=incident.id,
        detail={"incident_number": incident_number, "emergency_type": emergency_type},
    )

    # Send push notifications to commanders
    asyncio.create_task(
        send_push_to_commanders(
            db=db,
            org_id=org_id,
            incident=incident,
            initiator_name=initiator.name if initiator else "Unknown",
        )
    )

    return incident


async def get_incident_detail(db: AsyncSession, incident_id: UUID) -> dict | None:
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    incident = result.scalar_one_or_none()
    if not incident:
        return None

    participants_result = await db.execute(
        select(Participant).where(Participant.incident_id == incident_id)
    )
    participants = participants_result.scalars().all()

    messages_result = await db.execute(
        select(Message)
        .where(Message.incident_id == incident_id)
        .order_by(Message.seq.desc())
        .limit(50)
    )
    messages = list(reversed(messages_result.scalars().all()))

    actions_result = await db.execute(
        select(Action).where(
            and_(Action.incident_id == incident_id, Action.status == "pending")
        )
    )
    pending_actions = actions_result.scalars().all()

    return {
        "incident": incident,
        "participants": participants,
        "messages": messages,
        "pending_actions": pending_actions,
    }


async def resolve_incident(
    db: AsyncSession,
    incident: Incident,
    resolved_by: UUID,
    resolution_note: str = None,
) -> Incident:
    incident.status = "resolved"
    incident.resolved_at = datetime.now(timezone.utc)
    incident.resolved_by = resolved_by
    await db.commit()
    await db.refresh(incident)

    logger.info(f"Incident {incident.incident_number} resolved by {resolved_by}")

    await create_message(
        db=db,
        incident_id=incident.id,
        sender_type="system",
        message_type="closure",
        content=resolution_note or f"Incident {incident.incident_number} resolved.",
        metadata={"event": "incident.resolved", "resolved_by": str(resolved_by)},
    )

    await write_audit(
        db=db,
        org_id=incident.org_id,
        event_type="incident.resolved",
        actor_type="human",
        actor_id=resolved_by,
        incident_id=incident.id,
        detail={"incident_number": incident.incident_number, "resolution_note": resolution_note},
    )

    return incident
