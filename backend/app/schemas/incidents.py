from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class LocationIn(BaseModel):
    lat: float
    lng: float
    address: Optional[str] = None
    accuracy_m: Optional[float] = None


class PatientInfoIn(BaseModel):
    name: Optional[str] = None
    conditions: Optional[list] = []
    allergies: Optional[list] = []
    meds: Optional[list] = []
    emergency_contact: Optional[dict] = {}


class TriggerIncidentRequest(BaseModel):
    emergency_type: str  # workplace_violence, medical, other, generic
    trigger_source: str  # ui_button, voice, pendant, ai_detected, commander
    facility_id: Optional[UUID] = None
    location: Optional[LocationIn] = None
    patient_info: Optional[PatientInfoIn] = None


class ResolveIncidentRequest(BaseModel):
    resolution_note: Optional[str] = None


class ParticipantOut(BaseModel):
    id: UUID
    incident_id: UUID
    user_id: Optional[UUID] = None
    role: str
    name: str
    is_ai: bool
    joined_at: datetime
    left_at: Optional[datetime] = None
    last_location: Optional[dict] = {}
    dispatch_status: Optional[str] = None
    dispatch_eta_seconds: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class IncidentOut(BaseModel):
    id: UUID
    org_id: UUID
    incident_number: str
    status: str
    emergency_type: str
    trigger_source: str
    severity: int
    facility_id: Optional[UUID] = None
    sop_id: Optional[UUID] = None
    commander_id: Optional[UUID] = None
    initiated_by: UUID
    location: Optional[dict] = {}
    patient_info: Optional[dict] = {}
    ai_assessment: Optional[dict] = {}
    initiated_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentDetailOut(BaseModel):
    incident: IncidentOut
    participants: List[ParticipantOut]
    messages: List[dict]
    pending_actions: List[dict]
