"""
Integration tests for service layer — run against a real PostgreSQL DB.
Skip with: pytest -m "not integration"
Run with:  TEST_DATABASE_URL=... pytest tests/integration -m integration
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from datetime import datetime, timezone

from app.models import Incident, Participant, Action, Message, AuditLog, MonitoringSession, TelemetryEvent
from app.services.incident_service import create_incident, resolve_incident
from app.services.action_service import create_action, approve_action, reject_action
from app.services.thread_service import create_message, write_audit
from app.services.monitoring_service import start_session, end_session, submit_telemetry, get_session, get_telemetry
from app.schemas.monitoring import TelemetryEventIn


pytestmark = pytest.mark.integration


# ── create_incident ───────────────────────────────────────────────────────────

async def test_create_incident_creates_record(db: AsyncSession, seed):
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
        facility_id=seed["facility"].id,
    )
    assert incident.id is not None
    assert incident.status == "triggered"
    assert incident.emergency_type == "workplace_violence"
    assert incident.incident_number.startswith("INC-")


async def test_create_incident_assigns_sop(db: AsyncSession, seed):
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    assert incident.sop_id == seed["sop_wv"].id


async def test_create_incident_assigns_commander(db: AsyncSession, seed):
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
    )
    assert incident.commander_id == seed["commander"].id


async def test_create_incident_creates_participants(db: AsyncSession, seed):
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    result = await db.execute(
        select(Participant).where(Participant.incident_id == incident.id)
    )
    participants = result.scalars().all()
    roles = {p.role for p in participants}
    assert "initiator" in roles
    assert "commander" in roles
    assert "ai_agent" in roles


async def test_create_incident_creates_system_message(db: AsyncSession, seed):
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    result = await db.execute(
        select(Message).where(Message.incident_id == incident.id)
    )
    messages = result.scalars().all()
    assert len(messages) >= 1
    assert any(m.message_type == "system_event" for m in messages)


async def test_create_incident_writes_audit(db: AsyncSession, seed):
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    result = await db.execute(
        select(AuditLog).where(AuditLog.incident_id == incident.id)
    )
    logs = result.scalars().all()
    assert any(log.event_type == "incident.created" for log in logs)


# ── resolve_incident ──────────────────────────────────────────────────────────

async def test_resolve_incident_changes_status(db: AsyncSession, seed):
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    resolved = await resolve_incident(
        db=db, incident=incident,
        resolved_by=seed["commander"].id,
        resolution_note="Situation cleared."
    )
    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None
    assert resolved.resolved_by == seed["commander"].id


async def test_resolve_incident_creates_closure_message(db: AsyncSession, seed):
    incident = await create_incident(
        db=db,
        org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    await resolve_incident(db=db, incident=incident, resolved_by=seed["commander"].id)

    result = await db.execute(
        select(Message).where(Message.incident_id == incident.id)
    )
    messages = result.scalars().all()
    assert any(m.message_type == "closure" for m in messages)


# ── create_action ─────────────────────────────────────────────────────────────

async def test_create_action_creates_record(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    action = await create_action(
        db=db,
        incident_id=incident.id,
        org_id=seed["org"].id,
        action_type="dispatch_responder",
        description="Dispatch nearest responder",
        tier="amber",
    )
    assert action.id is not None
    assert action.status == "pending"
    assert action.tier == "amber"


async def test_approve_action_changes_status(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    action = await create_action(
        db=db, incident_id=incident.id, org_id=seed["org"].id,
        action_type="dispatch_responder", description="Dispatch",
        tier="amber",
    )
    approved = await approve_action(
        db=db, action=action, approved_by=seed["commander"].id
    )
    assert approved.status == "executed"
    assert approved.approved_by == seed["commander"].id


async def test_reject_action_changes_status(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    action = await create_action(
        db=db, incident_id=incident.id, org_id=seed["org"].id,
        action_type="contact_911", description="Call 911",
        tier="red",
    )
    rejected = await reject_action(
        db=db, action=action, rejected_by=seed["commander"].id,
        reason="Not necessary"
    )
    assert rejected.status == "rejected"
    assert "Not necessary" in str(rejected.detail)


# ── create_message ────────────────────────────────────────────────────────────

async def test_create_message_persists_to_db(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
    )
    msg = await create_message(
        db=db,
        incident_id=incident.id,
        sender_type="ai",
        message_type="system_event",
        content="SOP execution started.",
        metadata={"event": "sop.started"},
    )
    assert msg.id is not None
    assert msg.seq >= 1
    assert msg.content == "SOP execution started."


async def test_create_message_increments_seq(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
    )
    msg1 = await create_message(db=db, incident_id=incident.id, sender_type="ai",
                                 message_type="text", content="First")
    msg2 = await create_message(db=db, incident_id=incident.id, sender_type="ai",
                                 message_type="text", content="Second")
    assert msg2.seq > msg1.seq


# ── write_audit ───────────────────────────────────────────────────────────────

async def test_write_audit_persists(db: AsyncSession, seed):
    await write_audit(
        db=db,
        org_id=seed["org"].id,
        event_type="test.event",
        actor_type="system",
        detail={"key": "value"},
    )
    result = await db.execute(
        select(AuditLog).where(AuditLog.event_type == "test.event")
    )
    log = result.scalar_one_or_none()
    assert log is not None
    assert log.detail == {"key": "value"}


# ── monitoring: start_session ─────────────────────────────────────────────────

async def test_start_session_creates_record(db: AsyncSession, seed):
    session = await start_session(
        db=db,
        org_id=seed["org"].id,
        user_id=seed["worker"].id,
        check_in_interval_seconds=300,
    )
    assert session.id is not None
    assert session.status == "active"
    assert session.check_in_interval_seconds == 300
    assert session.last_check_in is not None


async def test_start_session_supersedes_existing(db: AsyncSession, seed):
    first = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    second = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)

    await db.refresh(first)
    assert first.status == "ended"
    assert first.end_reason == "superseded"
    assert second.status == "active"


async def test_start_session_writes_audit(db: AsyncSession, seed):
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    result = await db.execute(
        select(AuditLog).where(
            AuditLog.event_type == "monitoring.session_started",
            AuditLog.detail["session_id"].astext == str(session.id),
        )
    )
    log = result.scalars().first()
    assert log is not None


# ── monitoring: end_session ───────────────────────────────────────────────────

async def test_end_session_changes_status(db: AsyncSession, seed):
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    ended = await end_session(db=db, session=session, reason="manual")
    assert ended.status == "ended"
    assert ended.end_reason == "manual"
    assert ended.ended_at is not None


async def test_end_session_writes_audit(db: AsyncSession, seed):
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    await end_session(db=db, session=session, reason="manual")
    result = await db.execute(
        select(AuditLog).where(AuditLog.event_type == "monitoring.session_ended")
    )
    log = result.scalars().first()
    assert log is not None


# ── monitoring: submit_telemetry ──────────────────────────────────────────────

async def test_submit_telemetry_persists_events(db: AsyncSession, seed):
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    now = datetime.now(timezone.utc)
    events = [
        TelemetryEventIn(event_type="location", data={"lat": 37.77, "lng": -122.41, "accuracy_m": 5.0}, recorded_at=now),
        TelemetryEventIn(event_type="heart_rate", data={"bpm": 88}, recorded_at=now),
        TelemetryEventIn(event_type="speed", data={"kmh": 2.1}, recorded_at=now),
    ]
    result = await submit_telemetry(db=db, session=session, events=events)
    assert result["accepted"] == 3
    assert result["escalated"] is False
    assert result["incident_id"] is None

    stored = await get_telemetry(db, session.id)
    assert len(stored) == 3
    types = {e.event_type for e in stored}
    assert types == {"location", "heart_rate", "speed"}


async def test_submit_telemetry_updates_last_check_in(db: AsyncSession, seed):
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    before = session.last_check_in
    now = datetime.now(timezone.utc)
    await submit_telemetry(db=db, session=session, events=[
        TelemetryEventIn(event_type="location", data={"lat": 37.77, "lng": -122.41}, recorded_at=now),
    ])
    await db.refresh(session)
    assert session.last_check_in >= before


async def test_submit_fall_detected_escalates_session(db: AsyncSession, seed):
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    now = datetime.now(timezone.utc)
    result = await submit_telemetry(db=db, session=session, events=[
        TelemetryEventIn(
            event_type="fall_detected",
            data={"confidence": 0.97, "location": {"lat": 37.77, "lng": -122.41}},
            recorded_at=now,
        ),
    ])
    assert result["escalated"] is True
    assert result["incident_id"] is not None

    await db.refresh(session)
    assert session.status == "escalated"
    assert session.end_reason == "escalated"
    assert session.incident_id == result["incident_id"]

    # Incident should exist
    incident_result = await db.execute(
        select(Incident).where(Incident.id == result["incident_id"])
    )
    incident = incident_result.scalar_one_or_none()
    assert incident is not None
    assert incident.emergency_type == "medical"
    assert incident.trigger_source == "ai_detected"


async def test_submit_telemetry_no_escalation_if_already_escalated(db: AsyncSession, seed):
    """A second fall event on an already-escalated session should not create another incident."""
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    now = datetime.now(timezone.utc)
    fall_event = [TelemetryEventIn(event_type="fall_detected", data={"confidence": 0.9}, recorded_at=now)]

    first = await submit_telemetry(db=db, session=session, events=fall_event)
    assert first["escalated"] is True

    # Session is now escalated — submitting again should raise 400 (handled at router level)
    # At service level, status != "active" so escalation branch is skipped
    await db.refresh(session)
    second = await submit_telemetry(db=db, session=session, events=fall_event)
    assert second["escalated"] is False
    assert second["incident_id"] is None


# ── monitoring: get_telemetry ─────────────────────────────────────────────────

async def test_get_telemetry_filter_by_event_type(db: AsyncSession, seed):
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    now = datetime.now(timezone.utc)
    await submit_telemetry(db=db, session=session, events=[
        TelemetryEventIn(event_type="location", data={"lat": 37.77, "lng": -122.41}, recorded_at=now),
        TelemetryEventIn(event_type="heart_rate", data={"bpm": 72}, recorded_at=now),
    ])
    locations = await get_telemetry(db, session.id, event_type="location")
    assert len(locations) == 1
    assert locations[0].event_type == "location"


async def test_get_telemetry_filter_by_after(db: AsyncSession, seed):
    from datetime import timedelta
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)
    past = datetime(2020, 1, 1, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    await submit_telemetry(db=db, session=session, events=[
        TelemetryEventIn(event_type="location", data={"lat": 37.77, "lng": -122.41}, recorded_at=past),
        TelemetryEventIn(event_type="location", data={"lat": 37.78, "lng": -122.40}, recorded_at=now),
    ])
    recent = await get_telemetry(db, session.id, after=datetime(2024, 1, 1, tzinfo=timezone.utc))
    assert len(recent) == 1
    assert recent[0].data["lat"] == 37.78
