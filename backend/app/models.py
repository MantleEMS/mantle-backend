import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Text,
    ForeignKey, BigInteger, Float, UniqueConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    settings = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    facilities = relationship("Facility", back_populates="org", lazy="noload")
    users = relationship("User", back_populates="org", lazy="noload")
    sops = relationship("SOP", back_populates="org", lazy="noload")
    incidents = relationship("Incident", back_populates="org", lazy="noload")


class Facility(Base):
    __tablename__ = "facilities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    name = Column(String(255), nullable=False)
    # facility_type: patient_home, office, hospital, snf, other
    facility_type = Column(String(50), nullable=False, default="patient_home")
    address = Column(JSONB, default=dict)
    risk_flags = Column(JSONB, default=list)
    # cell_coverage: good, fair, poor, none, unknown
    cell_coverage = Column(String(20), default="unknown")
    nearest_hospital = Column(JSONB, default=dict)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    org = relationship("Organization", back_populates="facilities", lazy="noload")


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    phone = Column(String(50))
    # role: super_admin, org_admin, admin, commander, worker, responder, supervisor
    role = Column(String(30), nullable=False, default="worker")
    # status: active, on_duty, off_duty, inactive
    status = Column(String(30), nullable=False, default="active")
    qualifications = Column(JSONB, default=list)
    medical_flags = Column(JSONB, default=list)
    device_info = Column(JSONB, default=dict)
    last_location = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    org = relationship("Organization", back_populates="users", lazy="noload")

    __table_args__ = (
        Index("ix_users_org_role_status", "org_id", "role", "status"),
    )


class SOP(Base):
    __tablename__ = "sops"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    name = Column(String(255), nullable=False)
    sop_code = Column(String(50), nullable=False)
    # emergency_type: workplace_violence, medical, other, generic
    emergency_type = Column(String(50), nullable=False)
    description = Column(Text)
    steps = Column(JSONB, default=list)
    responder_checklist = Column(JSONB, default=list)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    org = relationship("Organization", back_populates="sops", lazy="noload")

    __table_args__ = (
        Index("ix_sops_org_emergency_type", "org_id", "emergency_type"),
    )


class Incident(Base):
    __tablename__ = "incidents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    incident_number = Column(String(30), unique=True, nullable=False)
    # status: triggered, active, resolved, cancelled
    status = Column(String(30), nullable=False, default="triggered")
    # emergency_type: workplace_violence, medical, other, generic
    emergency_type = Column(String(50), nullable=False)
    # trigger_source: ui_button, voice, pendant, ai_detected, commander
    trigger_source = Column(String(50), nullable=False)
    # severity: 1-5
    severity = Column(Integer, default=3)
    facility_id = Column(UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=True)
    sop_id = Column(UUID(as_uuid=True), ForeignKey("sops.id"), nullable=True)
    commander_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    initiated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    location = Column(JSONB, default=dict)
    patient_info = Column(JSONB, default=dict)
    ai_assessment = Column(JSONB, default=dict)
    initiated_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    org = relationship("Organization", back_populates="incidents", lazy="noload")
    facility = relationship("Facility", foreign_keys=[facility_id], lazy="noload")
    sop = relationship("SOP", foreign_keys=[sop_id], lazy="noload")
    commander = relationship("User", foreign_keys=[commander_id], lazy="noload")
    initiator = relationship("User", foreign_keys=[initiated_by], lazy="noload")
    resolver = relationship("User", foreign_keys=[resolved_by], lazy="noload")
    participants = relationship("Participant", back_populates="incident", lazy="noload")
    messages = relationship("Message", back_populates="incident", lazy="noload")
    actions = relationship("Action", back_populates="incident", lazy="noload")
    evidence = relationship("Evidence", back_populates="incident", lazy="noload")

    __table_args__ = (
        Index("ix_incidents_org_status", "org_id", "status"),
        Index("ix_incidents_initiated_by", "initiated_by"),
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # sender_type: human, ai, system
    sender_type = Column(String(20), nullable=False)
    # message_type: text, system_event, classification, action, evidence, status_update, command_transfer, closure
    message_type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False, default="")
    meta = Column("metadata", JSONB, default=dict)
    seq = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    incident = relationship("Incident", back_populates="messages", lazy="noload")
    sender = relationship("User", foreign_keys=[sender_id], lazy="noload")

    __table_args__ = (
        Index("ix_messages_incident_created", "incident_id", "created_at"),
    )


class Participant(Base):
    __tablename__ = "participants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # role: initiator, commander, responder, ai_agent
    role = Column(String(30), nullable=False)
    name = Column(String(255), nullable=False)
    is_ai = Column(Boolean, default=False)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    left_at = Column(DateTime(timezone=True), nullable=True)
    last_location = Column(JSONB, default=dict)
    # dispatch_status: pending, accepted, declined, en_route, arrived
    dispatch_status = Column(String(30), nullable=True)
    dispatch_eta_seconds = Column(Integer, nullable=True)

    incident = relationship("Incident", back_populates="participants", lazy="noload")
    user = relationship("User", foreign_keys=[user_id], lazy="noload")

    __table_args__ = (
        UniqueConstraint("incident_id", "user_id", name="uq_participant_incident_user"),
        Index("ix_participants_incident", "incident_id"),
    )


class Action(Base):
    __tablename__ = "actions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    sop_step = Column(Integer, nullable=True)
    # tier: green, amber, red
    tier = Column(String(20), nullable=False, default="green")
    # action_type: begin_recording, alert_commander, dispatch_responder, contact_911, notify_emergency_contact, resolve_incident
    action_type = Column(String(50), nullable=False)
    # status: pending, approved, rejected, executed, failed, expired
    status = Column(String(30), nullable=False, default="pending")
    description = Column(Text, nullable=False, default="")
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    executed_at = Column(DateTime(timezone=True), nullable=True)
    detail = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    incident = relationship("Incident", back_populates="actions", lazy="noload")
    assignee = relationship("User", foreign_keys=[assigned_to], lazy="noload")
    approver = relationship("User", foreign_keys=[approved_by], lazy="noload")

    __table_args__ = (
        Index("ix_actions_incident", "incident_id"),
        Index("ix_actions_pending", "status", postgresql_where=Column("status") == "pending"),
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # file_type: photo, audio, video, document
    file_type = Column(String(30), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False, default=0)
    sha256_hash = Column(String(64), nullable=False)
    duration_seconds = Column(Float, nullable=True)
    mime_type = Column(String(100), nullable=True)
    meta = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    incident = relationship("Incident", back_populates="evidence", lazy="noload")
    uploader = relationship("User", foreign_keys=[uploaded_by], lazy="noload")

    __table_args__ = (
        Index("ix_evidence_incident", "incident_id"),
    )


class MonitoringSession(Base):
    __tablename__ = "monitoring_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    # status: active | ended | escalated
    status = Column(String(30), nullable=False, default="active")
    # check_in_interval_seconds: if set, worker must check in within this window or alert is raised
    check_in_interval_seconds = Column(Integer, nullable=True)
    last_check_in = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    ended_at = Column(DateTime(timezone=True), nullable=True)
    # end_reason: manual | timeout | escalated | panic
    end_reason = Column(String(30), nullable=True)
    # populated if session was escalated to an incident
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True)
    meta = Column("metadata", JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", foreign_keys=[user_id], lazy="noload")
    telemetry = relationship("TelemetryEvent", back_populates="session", lazy="noload")

    __table_args__ = (
        Index("ix_monitoring_sessions_org_status", "org_id", "status"),
        Index("ix_monitoring_sessions_user", "user_id"),
    )


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("monitoring_sessions.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    # event_type: location | fall_detected | heart_rate | speed | custom
    event_type = Column(String(50), nullable=False)
    data = Column(JSONB, nullable=False, default=dict)
    # recorded_at: client-side timestamp; received_at: server-side
    recorded_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now())

    session = relationship("MonitoringSession", back_populates="telemetry", lazy="noload")

    __table_args__ = (
        Index("ix_telemetry_session_recorded", "session_id", "recorded_at"),
        Index("ix_telemetry_event_type", "session_id", "event_type"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True)
    # event_type: incident.created, incident.resolved, action.approved, action.rejected,
    #   evidence.uploaded, dispatch.accepted, dispatch.declined, sop.step_executed,
    #   user.sos_triggered, user.status_changed, 911.contacted
    event_type = Column(String(80), nullable=False)
    # actor_type: human, ai, system
    actor_type = Column(String(20), nullable=False, default="system")
    actor_id = Column(UUID(as_uuid=True), nullable=True)
    detail = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_audit_log_org_created", "org_id", "created_at"),
        Index("ix_audit_log_incident_created", "incident_id", "created_at"),
    )


class TrainingSample(Base):
    """
    Captures full LLM conversations for future fine-tuning.
    Each row is one complete agent run: system prompt + messages + tool calls + final answer.
    Export via scripts/export_training_data.py to produce OpenAI JSONL fine-tuning format.
    """
    __tablename__ = "training_samples"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    incident_id = Column(UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=True)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    # provider: anthropic | ollama | bedrock
    provider = Column(String(30), nullable=False)
    model = Column(String(100), nullable=False)
    emergency_type = Column(String(50), nullable=True)
    # quality: good | rejected — set via export script review
    quality = Column(String(20), nullable=False, default="good")
    success = Column(Boolean, nullable=False, default=True)
    iterations = Column(Integer, nullable=False, default=0)
    # full conversation in OpenAI message format (system + user + assistant + tool results)
    conversation = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_training_samples_quality", "quality"),
        Index("ix_training_samples_emergency_type", "emergency_type"),
    )
