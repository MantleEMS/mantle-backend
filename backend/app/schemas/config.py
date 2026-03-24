from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class OrganizationOut(BaseModel):
    id: UUID
    name: str
    slug: str
    settings: Optional[dict] = {}
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrganizationCreate(BaseModel):
    name: str
    slug: str
    settings: Optional[dict] = {}


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    settings: Optional[dict] = None


class FacilityOut(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    facility_type: str
    address: Optional[dict] = {}
    risk_flags: Optional[list] = []
    cell_coverage: Optional[str] = "unknown"
    nearest_hospital: Optional[dict] = {}
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FacilityCreate(BaseModel):
    name: str
    facility_type: str = "patient_home"
    address: Optional[dict] = {}
    risk_flags: Optional[list] = []
    cell_coverage: Optional[str] = "unknown"
    nearest_hospital: Optional[dict] = {}
    notes: Optional[str] = None
    org_id: Optional[UUID] = None  # only used when caller is super_admin


class FacilityUpdate(BaseModel):
    name: Optional[str] = None
    facility_type: Optional[str] = None
    address: Optional[dict] = None
    risk_flags: Optional[list] = None
    cell_coverage: Optional[str] = None
    nearest_hospital: Optional[dict] = None
    notes: Optional[str] = None


class SOPStepOut(BaseModel):
    step: int
    actor: str
    action: str
    auto: bool
    description: str
    target_time_sec: Optional[int] = None
    tier: Optional[str] = None


class ResponderChecklistItem(BaseModel):
    step: int
    text: str


class SOPOut(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    sop_code: str
    emergency_type: str
    description: Optional[str] = None
    steps: List[SOPStepOut] = []
    responder_checklist: List[ResponderChecklistItem] = []
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SOPCreate(BaseModel):
    name: str
    sop_code: str
    emergency_type: str
    description: Optional[str] = None
    steps: List[SOPStepOut] = []
    responder_checklist: List[ResponderChecklistItem] = []
    is_active: bool = True
    org_id: Optional[UUID] = None  # only used when caller is super_admin


class SOPUpdate(BaseModel):
    name: Optional[str] = None
    sop_code: Optional[str] = None
    emergency_type: Optional[str] = None
    description: Optional[str] = None
    steps: Optional[List[SOPStepOut]] = None
    responder_checklist: Optional[List[ResponderChecklistItem]] = None
    is_active: Optional[bool] = None


class UserOut(BaseModel):
    id: UUID
    org_id: Optional[UUID] = None
    email: str
    name: str
    phone: Optional[str] = None
    role: str
    status: str
    qualifications: Optional[list] = []
    medical_flags: Optional[list] = []
    device_info: Optional[dict] = {}
    last_location: Optional[dict] = {}
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    email: str
    password: str
    name: str
    phone: Optional[str] = None
    role: str = "worker"
    status: str = "active"
    qualifications: Optional[list] = []
    medical_flags: Optional[list] = []
    device_info: Optional[dict] = {}
    org_id: Optional[UUID] = None  # only used when caller is super_admin


class UserUpdate(BaseModel):
    email: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    qualifications: Optional[list] = None
    medical_flags: Optional[list] = None
    device_info: Optional[dict] = None
