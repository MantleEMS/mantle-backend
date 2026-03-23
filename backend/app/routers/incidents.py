import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import uuid

from app.database import get_db
from app.dependencies import get_current_user, require_commander
from app.models import User, Incident

logger = logging.getLogger(__name__)
from app.schemas.incidents import (
    TriggerIncidentRequest, ResolveIncidentRequest,
    IncidentOut, IncidentDetailOut, ParticipantOut,
)
from app.schemas.threads import MessageOut
from app.schemas.actions import ActionOut
from app.services.incident_service import create_incident, get_incident_detail, resolve_incident
from app.agent.router import agent_router

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


@router.post("", response_model=IncidentOut, status_code=201)
async def trigger_sos(
    body: TriggerIncidentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incident = await create_incident(
        db=db,
        org_id=current_user.org_id,
        initiated_by=current_user.id,
        emergency_type=body.emergency_type,
        trigger_source=body.trigger_source,
        facility_id=body.facility_id,
        location=body.location.model_dump() if body.location else {},
        patient_info=body.patient_info.model_dump() if body.patient_info else {},
    )
    logger.info(
        f"Incident {incident.incident_number} created by user {current_user.id} "
        f"[type={body.emergency_type}, source={body.trigger_source}, sop={'yes' if incident.sop_id else 'none'}]"
    )

    # Start agent SOP execution in background (scripted or LLM, per AI_MODE)
    if incident.sop_id:
        asyncio.create_task(
            agent_router.handle_incident(
                incident_id=incident.id,
                org_id=current_user.org_id,
                sop_id=incident.sop_id,
            )
        )
    else:
        logger.warning(f"Incident {incident.incident_number}: no SOP found for type '{body.emergency_type}', agent not started")

    return incident


@router.get("/{incident_id}")
async def get_incident(
    incident_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    detail = await get_incident_detail(db, incident_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Incident not found")

    incident = detail["incident"]
    if incident.org_id != current_user.org_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return {
        "incident": _incident_to_dict(incident),
        "participants": [_participant_to_dict(p) for p in detail["participants"]],
        "messages": [_message_to_dict(m) for m in detail["messages"]],
        "pending_actions": [_action_to_dict(a) for a in detail["pending_actions"]],
    }


@router.post("/{incident_id}/resolve")
async def resolve(
    incident_id: uuid.UUID,
    body: ResolveIncidentRequest,
    current_user: User = Depends(require_commander),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(
            and_(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        )
    )
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status in ("resolved", "cancelled"):
        logger.warning(f"Resolve rejected for incident {incident_id}: already in status '{incident.status}'")
        raise HTTPException(status_code=400, detail="Incident already resolved or cancelled")

    incident = await resolve_incident(
        db=db,
        incident=incident,
        resolved_by=current_user.id,
        resolution_note=body.resolution_note,
    )
    logger.info(f"Incident {incident.incident_number} resolved by user {current_user.id}")
    return {"status": incident.status, "resolved_at": incident.resolved_at.isoformat()}


@router.get("", response_model=list[IncidentOut])
async def list_incidents(
    status: str = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Incident).where(Incident.org_id == current_user.org_id)
    if status:
        query = query.where(Incident.status == status)
    query = query.order_by(Incident.initiated_at.desc()).limit(100)
    result = await db.execute(query)
    return result.scalars().all()


# --- Serialization helpers ---

def _incident_to_dict(i: Incident) -> dict:
    return {
        "id": str(i.id),
        "org_id": str(i.org_id),
        "incident_number": i.incident_number,
        "status": i.status,
        "emergency_type": i.emergency_type,
        "trigger_source": i.trigger_source,
        "severity": i.severity,
        "facility_id": str(i.facility_id) if i.facility_id else None,
        "sop_id": str(i.sop_id) if i.sop_id else None,
        "commander_id": str(i.commander_id) if i.commander_id else None,
        "initiated_by": str(i.initiated_by),
        "location": i.location or {},
        "patient_info": i.patient_info or {},
        "ai_assessment": i.ai_assessment or {},
        "initiated_at": i.initiated_at.isoformat() if i.initiated_at else None,
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "resolved_by": str(i.resolved_by) if i.resolved_by else None,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "updated_at": i.updated_at.isoformat() if i.updated_at else None,
    }


def _participant_to_dict(p) -> dict:
    return {
        "id": str(p.id),
        "incident_id": str(p.incident_id),
        "user_id": str(p.user_id) if p.user_id else None,
        "role": p.role,
        "name": p.name,
        "is_ai": p.is_ai,
        "joined_at": p.joined_at.isoformat() if p.joined_at else None,
        "last_location": p.last_location or {},
        "dispatch_status": p.dispatch_status,
        "dispatch_eta_seconds": p.dispatch_eta_seconds,
    }


def _message_to_dict(m) -> dict:
    return {
        "id": str(m.id),
        "sender_id": str(m.sender_id) if m.sender_id else None,
        "sender_type": m.sender_type,
        "message_type": m.message_type,
        "content": m.content,
        "metadata": m.meta or {},
        "seq": m.seq,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _action_to_dict(a) -> dict:
    return {
        "id": str(a.id),
        "sop_step": a.sop_step,
        "tier": a.tier,
        "action_type": a.action_type,
        "status": a.status,
        "description": a.description,
        "detail": a.detail or {},
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }
