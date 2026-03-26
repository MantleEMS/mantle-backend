import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user, user_has_role
from app.models import MonitoringSession, TelemetryEvent, User
from app.schemas.monitoring import (
    StartSessionRequest,
    EndSessionRequest,
    SubmitTelemetryRequest,
    SubmitTelemetryResponse,
    MonitoringSessionOut,
    TelemetryEventOut,
)
from app.services.monitoring_service import (
    start_session,
    end_session,
    submit_telemetry,
    get_session,
    get_telemetry,
)

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


@router.post("/sessions", response_model=MonitoringSessionOut, status_code=201)
async def start_monitoring_session(
    body: StartSessionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await start_session(
        db=db,
        org_id=current_user.org_id,
        user_id=current_user.id,
        check_in_interval_seconds=body.check_in_interval_seconds,
        metadata=body.metadata,
    )
    return session


@router.post("/sessions/{session_id}/end", response_model=MonitoringSessionOut)
async def end_monitoring_session(
    session_id: uuid.UUID,
    body: EndSessionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_session(db, session_id)
    if not session or session.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id and not user_has_role(current_user, "commander", "admin"):
        raise HTTPException(status_code=403, detail="Access denied")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Session is not active")

    session = await end_session(db, session, reason=body.reason)
    return session


@router.get("/sessions", response_model=list[MonitoringSessionOut])
async def list_monitoring_sessions(
    status: str = None,
    user_id: uuid.UUID = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(MonitoringSession).where(MonitoringSession.org_id == current_user.org_id)

    # Workers only see their own sessions; commanders/admins see all
    if not user_has_role(current_user, "commander", "admin", "supervisor"):
        query = query.where(MonitoringSession.user_id == current_user.id)
    elif user_id:
        query = query.where(MonitoringSession.user_id == user_id)

    if status:
        query = query.where(MonitoringSession.status == status)

    query = query.order_by(MonitoringSession.started_at.desc()).limit(100)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/sessions/{session_id}", response_model=MonitoringSessionOut)
async def get_monitoring_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_session(db, session_id)
    if not session or session.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id and not user_has_role(current_user, "commander", "admin", "supervisor"):
        raise HTTPException(status_code=403, detail="Access denied")
    return session


@router.post("/sessions/{session_id}/telemetry", response_model=SubmitTelemetryResponse)
async def submit_session_telemetry(
    session_id: uuid.UUID,
    body: SubmitTelemetryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_session(db, session_id)
    if not session or session.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="Session is not active")
    if not body.events:
        raise HTTPException(status_code=400, detail="No events provided")

    result = await submit_telemetry(db, session, body.events)
    return SubmitTelemetryResponse(
        accepted=result["accepted"],
        escalated=result["escalated"],
        incident_id=result["incident_id"],
    )


@router.get("/sessions/{session_id}/telemetry", response_model=list[TelemetryEventOut])
async def get_session_telemetry(
    session_id: uuid.UUID,
    event_type: str = None,
    after: datetime = None,
    limit: int = Query(default=200, le=1000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_session(db, session_id)
    if not session or session.org_id != current_user.org_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.user_id != current_user.id and not user_has_role(current_user, "commander", "admin", "supervisor"):
        raise HTTPException(status_code=403, detail="Access denied")

    events = await get_telemetry(db, session_id, event_type=event_type, limit=limit, after=after)
    return events
