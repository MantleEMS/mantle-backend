import json
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from datetime import datetime, timezone

from app.models import Message, AuditLog
from app.redis_client import get_redis, redis_incr, redis_publish
from app.metrics import messages_created as messages_created_metric


def _msg_to_dict(msg: Message) -> dict:
    return {
        "id": str(msg.id),
        "incident_id": str(msg.incident_id),
        "sender_id": str(msg.sender_id) if msg.sender_id else None,
        "sender_type": msg.sender_type,
        "message_type": msg.message_type,
        "content": msg.content,
        "metadata": msg.meta or {},
        "seq": msg.seq,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }


async def create_message(
    db: AsyncSession,
    incident_id: UUID,
    sender_type: str,
    message_type: str,
    content: str,
    metadata: dict = None,
    sender_id: UUID = None,
) -> Message:
    # Get next seq atomically from Redis
    seq = await redis_incr(f"thread:{incident_id}:seq")

    msg = Message(
        incident_id=incident_id,
        sender_id=sender_id,
        sender_type=sender_type,
        message_type=message_type,
        content=content,
        meta=metadata or {},
        seq=seq,
    )
    db.add(msg)
    await db.commit()
    await db.refresh(msg)

    messages_created_metric.labels(sender_type=sender_type).inc()

    # Publish to Redis for WebSocket broadcast
    event = {
        "type": "message.created",
        **_msg_to_dict(msg),
    }
    await redis_publish(f"thread:{incident_id}", json.dumps(event))

    return msg


async def get_messages(
    db: AsyncSession,
    incident_id: UUID,
    after: datetime = None,
    limit: int = 50,
) -> list[Message]:
    query = select(Message).where(Message.incident_id == incident_id)
    if after:
        query = query.where(Message.created_at > after)
    query = query.order_by(Message.seq.asc()).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def write_audit(
    db: AsyncSession,
    org_id: UUID | None,
    event_type: str,
    actor_type: str = "system",
    actor_id: UUID | None = None,
    incident_id: UUID | None = None,
    detail: dict = None,
):
    log = AuditLog(
        org_id=org_id,
        incident_id=incident_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        detail=detail or {},
    )
    db.add(log)
    await db.commit()
