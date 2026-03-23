import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import uuid

from app.database import get_db
from app.dependencies import get_current_user, require_commander
from app.models import User, Incident, Action
from app.schemas.actions import ApproveActionRequest, RejectActionRequest, ActionOut
from app.services.action_service import approve_action, reject_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/incidents", tags=["actions"])


@router.get("/{incident_id}/actions", response_model=list[ActionOut])
async def list_actions(
    incident_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify incident access
    inc_result = await db.execute(
        select(Incident).where(
            and_(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        )
    )
    if not inc_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Incident not found")

    result = await db.execute(
        select(Action)
        .where(Action.incident_id == incident_id)
        .order_by(Action.created_at.asc())
    )
    return result.scalars().all()


@router.post("/{incident_id}/actions/{action_id}/approve")
async def approve(
    incident_id: uuid.UUID,
    action_id: uuid.UUID,
    body: ApproveActionRequest,
    current_user: User = Depends(require_commander),
    db: AsyncSession = Depends(get_db),
):
    action = await _get_action(db, incident_id, action_id, current_user.org_id)
    if action.status != "pending":
        logger.warning(f"Approve rejected: action {action_id} is not pending (status={action.status}), user={current_user.id}")
        raise HTTPException(status_code=400, detail=f"Action is not pending (current: {action.status})")

    action = await approve_action(db, action, current_user.id, modifier=body.modifier)
    logger.info(f"Action {action_id} approved by user {current_user.id} on incident {incident_id}")
    return {"status": action.status, "approved_at": action.approved_at.isoformat()}


@router.post("/{incident_id}/actions/{action_id}/reject")
async def reject(
    incident_id: uuid.UUID,
    action_id: uuid.UUID,
    body: RejectActionRequest,
    current_user: User = Depends(require_commander),
    db: AsyncSession = Depends(get_db),
):
    action = await _get_action(db, incident_id, action_id, current_user.org_id)
    if action.status != "pending":
        logger.warning(f"Reject rejected: action {action_id} is not pending (status={action.status}), user={current_user.id}")
        raise HTTPException(status_code=400, detail=f"Action is not pending (current: {action.status})")

    action = await reject_action(db, action, current_user.id, reason=body.reason)
    logger.info(f"Action {action_id} rejected by user {current_user.id} on incident {incident_id} [reason={body.reason!r}]")
    return {"status": action.status}


async def _get_action(
    db: AsyncSession,
    incident_id: uuid.UUID,
    action_id: uuid.UUID,
    org_id: uuid.UUID,
) -> Action:
    # Verify incident belongs to org
    inc_result = await db.execute(
        select(Incident).where(
            and_(Incident.id == incident_id, Incident.org_id == org_id)
        )
    )
    if not inc_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Incident not found")

    result = await db.execute(
        select(Action).where(
            and_(Action.id == action_id, Action.incident_id == incident_id)
        )
    )
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")
    return action
