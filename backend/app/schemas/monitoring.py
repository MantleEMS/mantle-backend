import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class StartSessionRequest(BaseModel):
    check_in_interval_seconds: int | None = None
    metadata: dict = {}


class EndSessionRequest(BaseModel):
    reason: Literal["manual", "timeout", "panic"] = "manual"


class TelemetryEventIn(BaseModel):
    event_type: Literal["location", "fall_detected", "heart_rate", "speed", "custom"]
    data: dict[str, Any]
    recorded_at: datetime


class SubmitTelemetryRequest(BaseModel):
    events: list[TelemetryEventIn]


class MonitoringSessionOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    check_in_interval_seconds: int | None
    last_check_in: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    incident_id: uuid.UUID | None
    meta: dict
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class TelemetryEventOut(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    user_id: uuid.UUID
    org_id: uuid.UUID
    event_type: str
    data: dict[str, Any]
    recorded_at: datetime
    received_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class SubmitTelemetryResponse(BaseModel):
    accepted: int
    escalated: bool
    incident_id: uuid.UUID | None = None
