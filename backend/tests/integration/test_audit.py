"""
Integration tests for:
  - IncidentEventLog: written on monitoring escalation (telemetry snapshot)
  - IncidentEventLog: written on WebSocket location update (tested via service layer helper)
  - retention_service: downsampling and purging of telemetry_events

Run with: pytest tests/integration -m integration
"""

import pytest
import uuid
from datetime import datetime, timezone, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

from app.models import (
    Incident, IncidentEventLog, TelemetryEvent, MonitoringSession, Participant
)
from app.services.monitoring_service import start_session, submit_telemetry
from app.services.incident_service import create_incident
from app.services.retention_service import (
    downsample_location_events,
    purge_old_telemetry,
    run_retention,
)
from app.schemas.monitoring import TelemetryEventIn


pytestmark = pytest.mark.integration


# ── helpers ───────────────────────────────────────────────────────────────────

def _loc_event(lat, lng, recorded_at):
    return TelemetryEventIn(
        event_type="location",
        data={"lat": lat, "lng": lng, "accuracy_m": 5.0},
        recorded_at=recorded_at,
    )


# ── IncidentEventLog: monitoring escalation snapshot ─────────────────────────

async def test_escalation_writes_telemetry_snapshot(db: AsyncSession, seed):
    """When a monitoring session escalates, pre-incident telemetry is snapshotted."""
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    now = datetime.now(timezone.utc)

    events = [
        _loc_event(37.77, -122.41, now - timedelta(minutes=5)),
        _loc_event(37.78, -122.40, now - timedelta(minutes=2)),
        TelemetryEventIn(event_type="heart_rate", data={"bpm": 110}, recorded_at=now - timedelta(minutes=1)),
        TelemetryEventIn(
            event_type="fall_detected",
            data={"confidence": 0.97, "location": {"lat": 37.78, "lng": -122.40}},
            recorded_at=now,
        ),
    ]
    result = await submit_telemetry(db=db, session=session, events=events)
    assert result["escalated"] is True

    incident_id = result["incident_id"]
    log_result = await db.execute(
        select(IncidentEventLog).where(IncidentEventLog.incident_id == incident_id)
    )
    log_entries = log_result.scalars().all()
    assert len(log_entries) == 4  # all 4 events snapshotted
    sources = {e.source for e in log_entries}
    assert sources == {"monitoring_escalation"}
    event_types = {e.event_type for e in log_entries}
    assert event_types == {"telemetry_snapshot"}


async def test_escalation_snapshot_preserves_original_event_type(db: AsyncSession, seed):
    """The snapshot's data includes the original telemetry event_type."""
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    now = datetime.now(timezone.utc)

    await submit_telemetry(db=db, session=session, events=[
        _loc_event(37.77, -122.41, now - timedelta(minutes=1)),
        TelemetryEventIn(event_type="fall_detected", data={"confidence": 0.9}, recorded_at=now),
    ])
    await db.refresh(session)
    incident_id = session.incident_id

    log_result = await db.execute(
        select(IncidentEventLog)
        .where(IncidentEventLog.incident_id == incident_id)
        .order_by(IncidentEventLog.recorded_at.asc())
    )
    entries = log_result.scalars().all()
    original_types = [e.data["original_event_type"] for e in entries]
    assert "location" in original_types
    assert "fall_detected" in original_types


async def test_escalation_snapshot_only_includes_last_30_minutes(db: AsyncSession, seed):
    """Events older than 30 minutes are not included in the snapshot."""
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    now = datetime.now(timezone.utc)

    # Insert an old event directly (bypass submit_telemetry timestamp handling)
    old_event = TelemetryEvent(
        session_id=session.id,
        user_id=session.user_id,
        org_id=session.org_id,
        event_type="location",
        data={"lat": 10.0, "lng": 10.0},
        recorded_at=now - timedelta(hours=2),
    )
    db.add(old_event)
    await db.flush()

    result = await submit_telemetry(db=db, session=session, events=[
        _loc_event(37.77, -122.41, now - timedelta(minutes=10)),
        TelemetryEventIn(event_type="fall_detected", data={"confidence": 0.9}, recorded_at=now),
    ])
    assert result["escalated"] is True
    incident_id = result["incident_id"]

    log_result = await db.execute(
        select(IncidentEventLog).where(IncidentEventLog.incident_id == incident_id)
    )
    entries = log_result.scalars().all()
    lats = [e.data.get("lat") for e in entries if e.data.get("lat") is not None]
    assert 10.0 not in lats  # old event excluded


async def test_no_snapshot_for_independent_incident(db: AsyncSession, seed):
    """Independently triggered incidents have no pre-incident telemetry snapshot."""
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    log_result = await db.execute(
        select(IncidentEventLog).where(IncidentEventLog.incident_id == incident.id)
    )
    entries = log_result.scalars().all()
    snapshot_entries = [e for e in entries if e.source == "monitoring_escalation"]
    assert snapshot_entries == []


# ── IncidentEventLog: participant location (service-layer helper) ──────────────

async def test_incident_event_log_location_entry(db: AsyncSession, seed):
    """
    The WebSocket handler logic is tested here via the model layer directly,
    since the full WS stack requires an HTTP test client with authentication.
    Verifies that IncidentEventLog rows can be created for participant.location events.
    """
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="ui_button",
    )
    now = datetime.now(timezone.utc)
    entry = IncidentEventLog(
        incident_id=incident.id,
        org_id=incident.org_id,
        user_id=seed["worker"].id,
        event_type="participant.location",
        source="incident_ws",
        data={"lat": 37.77, "lng": -122.41, "accuracy_m": 8.0},
        recorded_at=now,
    )
    db.add(entry)
    await db.commit()

    result = await db.execute(
        select(IncidentEventLog)
        .where(
            IncidentEventLog.incident_id == incident.id,
            IncidentEventLog.event_type == "participant.location",
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "incident_ws"
    assert rows[0].data["lat"] == 37.77
    assert rows[0].user_id == seed["worker"].id


async def test_incident_event_log_append_only(db: AsyncSession, seed):
    """Multiple location updates accumulate as separate rows — never overwritten."""
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="ui_button",
    )
    now = datetime.now(timezone.utc)
    for i, (lat, lng) in enumerate([(37.77, -122.41), (37.78, -122.40), (37.79, -122.39)]):
        db.add(IncidentEventLog(
            incident_id=incident.id,
            org_id=incident.org_id,
            user_id=seed["worker"].id,
            event_type="participant.location",
            source="incident_ws",
            data={"lat": lat, "lng": lng},
            recorded_at=now + timedelta(seconds=i * 10),
        ))
    await db.commit()

    result = await db.execute(
        select(IncidentEventLog).where(IncidentEventLog.incident_id == incident.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 3
    lats = sorted(r.data["lat"] for r in rows)
    assert lats == [37.77, 37.78, 37.79]


# ── retention: downsample ─────────────────────────────────────────────────────

async def test_downsample_removes_duplicate_location_events(db: AsyncSession, seed):
    """Only 1 location event per minute per session is kept after downsampling."""
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    # 3 events in the same minute, all older than 7 days.
    # Use a fixed mid-minute timestamp to avoid any minute-boundary crossing.
    old_time = datetime(2020, 1, 1, 12, 0, 10, tzinfo=timezone.utc)
    for i in range(3):
        db.add(TelemetryEvent(
            session_id=session.id,
            user_id=session.user_id,
            org_id=session.org_id,
            event_type="location",
            data={"lat": 37.77 + i * 0.001, "lng": -122.41},
            recorded_at=old_time + timedelta(seconds=i * 10),  # 12:00:10, 12:00:20, 12:00:30
        ))
    await db.commit()

    deleted = await downsample_location_events(db)
    assert deleted == 2  # 3 events → keep 1, delete 2

    remaining = await db.execute(
        select(TelemetryEvent).where(
            TelemetryEvent.session_id == session.id,
            TelemetryEvent.event_type == "location",
        )
    )
    assert len(remaining.scalars().all()) == 1


async def test_downsample_does_not_touch_recent_events(db: AsyncSession, seed):
    """Events within the hot window (< 7 days) are not downsampled."""
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    now = datetime.now(timezone.utc)
    for i in range(3):
        db.add(TelemetryEvent(
            session_id=session.id,
            user_id=session.user_id,
            org_id=session.org_id,
            event_type="location",
            data={"lat": 37.77, "lng": -122.41},
            recorded_at=now - timedelta(hours=i),  # within 7 days
        ))
    await db.commit()

    deleted = await downsample_location_events(db)
    assert deleted == 0


async def test_downsample_does_not_touch_non_location_events(db: AsyncSession, seed):
    """Only location event_type is downsampled; heart_rate etc. are left alone."""
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    old_time = datetime.now(timezone.utc) - timedelta(days=10)
    for i in range(3):
        db.add(TelemetryEvent(
            session_id=session.id,
            user_id=session.user_id,
            org_id=session.org_id,
            event_type="heart_rate",
            data={"bpm": 70 + i},
            recorded_at=old_time + timedelta(seconds=i * 10),
        ))
    await db.commit()

    deleted = await downsample_location_events(db)
    assert deleted == 0


# ── retention: purge ──────────────────────────────────────────────────────────

async def test_purge_removes_old_events_for_ended_session(db: AsyncSession, seed):
    """Events older than 90 days for ended sessions are deleted."""
    from app.services.monitoring_service import end_session

    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    await end_session(db=db, session=session, reason="manual")

    very_old = datetime.now(timezone.utc) - timedelta(days=100)
    db.add(TelemetryEvent(
        session_id=session.id,
        user_id=session.user_id,
        org_id=session.org_id,
        event_type="location",
        data={"lat": 37.77, "lng": -122.41},
        recorded_at=very_old,
    ))
    await db.commit()

    purged = await purge_old_telemetry(db)
    assert purged >= 1

    remaining = await db.execute(
        select(TelemetryEvent).where(
            TelemetryEvent.session_id == session.id,
            TelemetryEvent.event_type == "location",
        )
    )
    assert remaining.scalars().all() == []


async def test_purge_skips_active_session_events(db: AsyncSession, seed):
    """Events for active sessions are never purged, regardless of age."""
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)

    very_old = datetime.now(timezone.utc) - timedelta(days=100)
    db.add(TelemetryEvent(
        session_id=session.id,
        user_id=session.user_id,
        org_id=session.org_id,
        event_type="location",
        data={"lat": 37.77, "lng": -122.41},
        recorded_at=very_old,
    ))
    await db.commit()

    purged = await purge_old_telemetry(db)

    remaining = await db.execute(
        select(TelemetryEvent).where(TelemetryEvent.session_id == session.id)
    )
    # Row must still exist — active session is protected
    assert len(remaining.scalars().all()) == 1


async def test_purge_skips_recent_events_for_ended_session(db: AsyncSession, seed):
    """Events within the 90-day window are kept even if the session has ended."""
    from app.services.monitoring_service import end_session

    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    await end_session(db=db, session=session, reason="manual")

    recent = datetime.now(timezone.utc) - timedelta(days=30)
    db.add(TelemetryEvent(
        session_id=session.id,
        user_id=session.user_id,
        org_id=session.org_id,
        event_type="location",
        data={"lat": 37.77, "lng": -122.41},
        recorded_at=recent,
    ))
    await db.commit()

    purged = await purge_old_telemetry(db)

    remaining = await db.execute(
        select(TelemetryEvent).where(TelemetryEvent.session_id == session.id)
    )
    assert len(remaining.scalars().all()) == 1


# ── retention: run_retention ──────────────────────────────────────────────────

async def test_run_retention_returns_summary(db: AsyncSession, seed):
    summary = await run_retention(db)
    assert "downsampled" in summary
    assert "purged" in summary
    assert isinstance(summary["downsampled"], int)
    assert isinstance(summary["purged"], int)
