import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import uuid

from app.database import get_db, AsyncSessionLocal
from app.dependencies import get_current_user, decode_token
from app.models import User, Incident, Message, Participant, IncidentEventLog
from app.schemas.threads import PostMessageRequest, MessageOut
from app.services.thread_service import create_message, get_messages
from app.redis_client import get_redis, redis_setex

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/incidents", tags=["threads"])


@router.post("/{incident_id}/messages", response_model=MessageOut, status_code=201)
async def post_message(
    incident_id: uuid.UUID,
    body: PostMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify incident exists and belongs to org
    result = await db.execute(
        select(Incident).where(
            and_(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        )
    )
    incident = result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    msg = await create_message(
        db=db,
        incident_id=incident_id,
        sender_type="human",
        message_type=body.message_type,
        content=body.content,
        metadata=body.metadata or {},
        sender_id=current_user.id,
    )
    return msg


@router.get("/{incident_id}/messages")
async def list_messages(
    incident_id: uuid.UUID,
    after: Optional[datetime] = None,
    limit: int = Query(default=50, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Incident).where(
            and_(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Incident not found")

    messages = await get_messages(db, incident_id, after=after, limit=limit)
    return [
        {
            "id": str(m.id),
            "incident_id": str(m.incident_id),
            "sender_id": str(m.sender_id) if m.sender_id else None,
            "sender_type": m.sender_type,
            "message_type": m.message_type,
            "content": m.content,
            "metadata": m.meta or {},
            "seq": m.seq,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


@router.websocket("/{incident_id}/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    incident_id: uuid.UUID,
    token: str = Query(..., description="JWT access token (query param, required for WS auth)"),
    last_seq: Optional[int] = Query(default=None, description="Resume from this message sequence number"),
):
    """
    Incident real-time WebSocket.

    Connect: `ws://.../api/v1/incidents/{incident_id}/ws?token=<jwt>`

    On connect the server sends a full **snapshot** of current incident state.
    After that, all messages are pushed as JSON events.

    ---
    **Client → Server messages**

    Location update (sent periodically while participant is active):
    ```json
    {"type": "location", "lat": 37.7749, "lng": -122.4194, "accuracy_m": 5.0}
    ```

    Heartbeat (sent every ~20 s to maintain presence):
    ```json
    {"type": "heartbeat"}
    ```

    ---
    **Server → Client events** (pushed via Redis pub/sub)

    | `type` | Description |
    |---|---|
    | `snapshot` | Full incident state on connect |
    | `participant.location` | Another participant moved |
    | `message.new` | New chat/system message |
    | `action.new` | New action requiring approval |
    | `incident.updated` | Status or field change |
    """
    # Authenticate via token query param
    payload = decode_token(token)
    user_id_str = payload.get("sub")
    if not user_id_str:
        await websocket.close(code=4001)
        return

    await websocket.accept()

    async with AsyncSessionLocal() as db:
        user_result = await db.execute(select(User).where(User.id == uuid.UUID(user_id_str)))
        user = user_result.scalar_one_or_none()
        if not user:
            await websocket.close(code=4001)
            return

        # Verify incident access
        inc_result = await db.execute(
            select(Incident).where(
                and_(Incident.id == incident_id, Incident.org_id == user.org_id)
            )
        )
        incident = inc_result.scalar_one_or_none()
        if not incident:
            await websocket.close(code=4004)
            return

        # Send initial snapshot
        from app.services.incident_service import get_incident_detail
        detail = await get_incident_detail(db, incident_id)
        if detail:
            snapshot = {
                "type": "snapshot",
                "incident": _incident_dict(detail["incident"]),
                "participants": [_participant_dict(p) for p in detail["participants"]],
                "messages": [_message_dict(m) for m in detail["messages"]],
                "pending_actions": [_action_dict(a) for a in detail["pending_actions"]],
            }
            await websocket.send_text(json.dumps(snapshot))

    # Subscribe to Redis pub/sub channel
    redis = await get_redis()
    pubsub = redis.pubsub()
    channel = f"thread:{incident_id}"
    await pubsub.subscribe(channel)

    logger.info(f"WS connected: user={user_id_str} incident={incident_id}")

    async def receive_from_client():
        """Handle client → server messages (heartbeat, location)."""
        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                msg_type = msg.get("type")

                if msg_type == "heartbeat":
                    # Refresh presence key with 30s TTL
                    await redis_setex(
                        f"presence:{incident_id}:{user_id_str}", 30, "1"
                    )

                elif msg_type == "location":
                    # Update participant location and broadcast
                    lat = msg.get("lat")
                    lng = msg.get("lng")
                    accuracy = msg.get("accuracy_m")
                    async with AsyncSessionLocal() as db:
                        participant_result = await db.execute(
                            select(Participant).where(
                                and_(
                                    Participant.incident_id == incident_id,
                                    Participant.user_id == uuid.UUID(user_id_str),
                                )
                            )
                        )
                        participant = participant_result.scalar_one_or_none()
                        if participant:
                            from datetime import timezone
                            now = datetime.now(timezone.utc)
                            participant.last_location = {
                                "lat": lat,
                                "lng": lng,
                                "accuracy_m": accuracy,
                                "updated_at": now.isoformat(),
                            }
                            # Append to incident audit trail (never overwritten)
                            incident_result = await db.execute(
                                select(Incident).where(Incident.id == incident_id)
                            )
                            incident = incident_result.scalar_one_or_none()
                            if incident:
                                db.add(IncidentEventLog(
                                    incident_id=incident_id,
                                    org_id=incident.org_id,
                                    user_id=uuid.UUID(user_id_str),
                                    event_type="participant.location",
                                    source="incident_ws",
                                    data={"lat": lat, "lng": lng, "accuracy_m": accuracy},
                                    recorded_at=now,
                                ))
                            await db.commit()

                    # Broadcast location event
                    location_event = json.dumps({
                        "type": "participant.location",
                        "user_id": user_id_str,
                        "lat": lat,
                        "lng": lng,
                        "updated_at": datetime.utcnow().isoformat(),
                    })
                    await redis.publish(channel, location_event)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"WS receive error: {e}")

    async def forward_from_redis():
        """Forward Redis pub/sub messages to WebSocket client."""
        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await websocket.send_text(message["data"])
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error(f"WS redis forward error: {e}")

    receive_task = asyncio.create_task(receive_from_client())
    forward_task = asyncio.create_task(forward_from_redis())

    try:
        done, pending = await asyncio.wait(
            [receive_task, forward_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except Exception as e:
        logger.error(f"WS error: {e}")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        logger.info(f"WS disconnected: user={user_id_str} incident={incident_id}")


# --- Serialization helpers ---

def _incident_dict(i) -> dict:
    return {
        "id": str(i.id),
        "incident_number": i.incident_number,
        "status": i.status,
        "emergency_type": i.emergency_type,
        "severity": i.severity,
        "initiated_at": i.initiated_at.isoformat() if i.initiated_at else None,
    }


def _participant_dict(p) -> dict:
    return {
        "id": str(p.id),
        "user_id": str(p.user_id) if p.user_id else None,
        "role": p.role,
        "name": p.name,
        "is_ai": p.is_ai,
        "dispatch_status": p.dispatch_status,
        "last_location": p.last_location or {},
    }


def _message_dict(m) -> dict:
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


def _action_dict(a) -> dict:
    return {
        "id": str(a.id),
        "tier": a.tier,
        "action_type": a.action_type,
        "status": a.status,
        "description": a.description,
        "detail": a.detail or {},
    }
