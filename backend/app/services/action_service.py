import json
import logging
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.models import Action, Incident
from app.redis_client import redis_zadd, redis_zrem, redis_publish
from app.services.thread_service import create_message, write_audit
from app.metrics import actions_created as actions_created_metric, actions_approved as actions_approved_metric, actions_rejected as actions_rejected_metric

logger = logging.getLogger(__name__)


async def create_action(
    db: AsyncSession,
    incident_id: UUID,
    org_id: UUID,
    action_type: str,
    description: str,
    tier: str = "green",
    sop_step: int = None,
    assigned_to: UUID = None,
    detail: dict = None,
) -> Action:
    action = Action(
        incident_id=incident_id,
        sop_step=sop_step,
        tier=tier,
        action_type=action_type,
        status="pending",
        description=description,
        assigned_to=assigned_to,
        detail=detail or {},
    )
    db.add(action)
    await db.commit()
    await db.refresh(action)

    actions_created_metric.labels(tier=tier).inc()
    logger.info(f"Action {action.id} created: type={action_type}, tier={tier}, incident={incident_id}")

    # Add to Redis urgency sorted set (score = timestamp)
    score = datetime.now(timezone.utc).timestamp()
    await redis_zadd("action_urgency", {str(action.id): score})

    # Broadcast action.created via Redis pub/sub
    event = {
        "type": "action.created",
        "id": str(action.id),
        "incident_id": str(incident_id),
        "tier": tier,
        "action_type": action_type,
        "status": "pending",
        "description": description,
        "detail": detail or {},
    }
    await redis_publish(f"thread:{incident_id}", json.dumps(event))

    return action


async def approve_action(
    db: AsyncSession,
    action: Action,
    approved_by: UUID,
    modifier: dict = None,
) -> Action:
    action.status = "approved"
    action.approved_by = approved_by
    action.approved_at = datetime.now(timezone.utc)
    if modifier:
        action.detail = {**(action.detail or {}), **modifier}
    await db.commit()
    await db.refresh(action)

    # Remove from urgency set
    await redis_zrem("action_urgency", str(action.id))

    # Broadcast action.updated
    event = {
        "type": "action.updated",
        "id": str(action.id),
        "incident_id": str(action.incident_id),
        "status": "approved",
        "approved_by": str(approved_by),
        "approved_at": action.approved_at.isoformat(),
    }
    await redis_publish(f"thread:{action.incident_id}", json.dumps(event))

    await write_audit(
        db=db,
        org_id=None,
        event_type="action.approved",
        actor_type="human",
        actor_id=approved_by,
        incident_id=action.incident_id,
        detail={"action_id": str(action.id), "action_type": action.action_type},
    )

    # Post action message to thread
    await create_message(
        db=db,
        incident_id=action.incident_id,
        sender_type="human",
        message_type="action",
        content=f"Action approved: {action.description}",
        metadata={"action_id": str(action.id), "action_type": action.action_type, "tier": action.tier, "status": "approved"},
        sender_id=approved_by,
    )

    # Mark as executed
    action.status = "executed"
    action.executed_at = datetime.now(timezone.utc)
    await db.commit()

    actions_approved_metric.inc()
    logger.info(f"Action {action.id} approved and executed by {approved_by} on incident {action.incident_id}")
    return action


async def reject_action(
    db: AsyncSession,
    action: Action,
    rejected_by: UUID,
    reason: str = None,
) -> Action:
    action.status = "rejected"
    action.approved_by = rejected_by
    action.approved_at = datetime.now(timezone.utc)
    if reason:
        action.detail = {**(action.detail or {}), "rejection_reason": reason}
    await db.commit()
    await db.refresh(action)

    # Remove from urgency set
    await redis_zrem("action_urgency", str(action.id))

    # Broadcast action.updated
    event = {
        "type": "action.updated",
        "id": str(action.id),
        "incident_id": str(action.incident_id),
        "status": "rejected",
        "rejected_by": str(rejected_by),
    }
    await redis_publish(f"thread:{action.incident_id}", json.dumps(event))

    await write_audit(
        db=db,
        org_id=None,
        event_type="action.rejected",
        actor_type="human",
        actor_id=rejected_by,
        incident_id=action.incident_id,
        detail={"action_id": str(action.id), "reason": reason},
    )

    await create_message(
        db=db,
        incident_id=action.incident_id,
        sender_type="human",
        message_type="action",
        content=f"Action rejected: {action.description}. Reason: {reason or 'Not specified'}",
        metadata={"action_id": str(action.id), "action_type": action.action_type, "tier": action.tier, "status": "rejected"},
        sender_id=rejected_by,
    )

    actions_rejected_metric.inc()
    logger.info(f"Action {action.id} rejected by {rejected_by} on incident {action.incident_id} [reason={reason!r}]")
    return action
