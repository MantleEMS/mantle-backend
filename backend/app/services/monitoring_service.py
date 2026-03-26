import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MonitoringSession, TelemetryEvent, IncidentEventLog
from app.schemas.monitoring import TelemetryEventIn
from app.services.thread_service import write_audit

logger = logging.getLogger(__name__)


async def start_session(
    db: AsyncSession,
    org_id: UUID,
    user_id: UUID,
    check_in_interval_seconds: int | None = None,
    metadata: dict = None,
) -> MonitoringSession:
    # End any existing active session for this user
    existing = await db.execute(
        select(MonitoringSession).where(
            and_(
                MonitoringSession.user_id == user_id,
                MonitoringSession.status == "active",
            )
        )
    )
    for session in existing.scalars().all():
        session.status = "ended"
        session.ended_at = datetime.now(timezone.utc)
        session.end_reason = "superseded"

    session = MonitoringSession(
        org_id=org_id,
        user_id=user_id,
        status="active",
        check_in_interval_seconds=check_in_interval_seconds,
        last_check_in=datetime.now(timezone.utc),
        meta=metadata or {},
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    await write_audit(
        db=db,
        org_id=org_id,
        event_type="monitoring.session_started",
        actor_type="human",
        actor_id=user_id,
        detail={"session_id": str(session.id), "check_in_interval_seconds": check_in_interval_seconds},
    )

    return session


async def end_session(
    db: AsyncSession,
    session: MonitoringSession,
    reason: str = "manual",
) -> MonitoringSession:
    session.status = "ended"
    session.ended_at = datetime.now(timezone.utc)
    session.end_reason = reason
    await db.commit()
    await db.refresh(session)

    await write_audit(
        db=db,
        org_id=session.org_id,
        event_type="monitoring.session_ended",
        actor_type="human",
        actor_id=session.user_id,
        detail={"session_id": str(session.id), "reason": reason},
    )

    return session


async def submit_telemetry(
    db: AsyncSession,
    session: MonitoringSession,
    events: list[TelemetryEventIn],
) -> dict:
    """
    Persist telemetry events. If a fall_detected event is present, escalate
    the session by creating an incident and updating session status.
    Returns a dict with accepted count, escalated flag, and incident_id if any.
    """
    now = datetime.now(timezone.utc)
    session.last_check_in = now

    records = []
    has_fall = False

    for ev in events:
        logger.trace(  # type: ignore[attr-defined]
            "Telemetry received: session=%s user=%s event_type=%s recorded_at=%s data=%s",
            session.id, session.user_id, ev.event_type, ev.recorded_at, ev.data,
        )
        records.append(
            TelemetryEvent(
                session_id=session.id,
                user_id=session.user_id,
                org_id=session.org_id,
                event_type=ev.event_type,
                data=ev.data,
                recorded_at=ev.recorded_at,
            )
        )
        if ev.event_type == "fall_detected":
            has_fall = True

    db.add_all(records)

    incident_id = None
    if has_fall and session.status == "active":
        logger.trace(  # type: ignore[attr-defined]
            "Fall detected — escalating session=%s user=%s to incident", session.id, session.user_id
        )
        # Escalate: create an incident for the fall
        from app.services.incident_service import create_incident

        # Determine location from the fall_detected event or the latest location event
        location = {}
        for ev in reversed(events):
            if ev.event_type == "fall_detected" and "location" in ev.data:
                location = ev.data["location"]
                break
            if ev.event_type == "location":
                location = {
                    "lat": ev.data.get("lat"),
                    "lng": ev.data.get("lng"),
                    "accuracy_m": ev.data.get("accuracy_m"),
                }
                break

        incident = await create_incident(
            db=db,
            org_id=session.org_id,
            initiated_by=session.user_id,
            emergency_type="medical",
            trigger_source="ai_detected",
            location=location,
            patient_info={},
        )
        incident_id = incident.id
        session.status = "escalated"
        session.end_reason = "escalated"
        session.ended_at = now
        session.incident_id = incident_id

        # Snapshot the last 30 minutes of telemetry into the incident audit trail.
        # This preserves pre-incident context for any incident, regardless of origin.
        from datetime import timedelta
        snapshot_after = now - timedelta(minutes=30)
        snapshot_result = await db.execute(
            select(TelemetryEvent)
            .where(
                and_(
                    TelemetryEvent.session_id == session.id,
                    TelemetryEvent.recorded_at >= snapshot_after,
                )
            )
            .order_by(TelemetryEvent.recorded_at.asc())
        )
        snapshot_events = snapshot_result.scalars().all()
        for te in snapshot_events:
            db.add(IncidentEventLog(
                incident_id=incident_id,
                org_id=session.org_id,
                user_id=session.user_id,
                event_type="telemetry_snapshot",
                source="monitoring_escalation",
                data={"original_event_type": te.event_type, **te.data},
                recorded_at=te.recorded_at,
            ))
            logger.trace(  # type: ignore[attr-defined]
                "IncidentEventLog written: incident=%s user=%s event_type=telemetry_snapshot source=monitoring_escalation original_event_type=%s recorded_at=%s",
                incident_id, session.user_id, te.event_type, te.recorded_at,
            )

    await db.commit()

    return {
        "accepted": len(records),
        "escalated": has_fall and incident_id is not None,
        "incident_id": incident_id,
    }


async def get_session(
    db: AsyncSession,
    session_id: UUID,
) -> MonitoringSession | None:
    result = await db.execute(
        select(MonitoringSession).where(MonitoringSession.id == session_id)
    )
    return result.scalar_one_or_none()


async def get_telemetry(
    db: AsyncSession,
    session_id: UUID,
    event_type: str | None = None,
    limit: int = 200,
    after: datetime | None = None,
) -> list[TelemetryEvent]:
    query = select(TelemetryEvent).where(TelemetryEvent.session_id == session_id)
    if event_type:
        query = query.where(TelemetryEvent.event_type == event_type)
    if after:
        query = query.where(TelemetryEvent.recorded_at > after)
    query = query.order_by(TelemetryEvent.recorded_at.asc()).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()
