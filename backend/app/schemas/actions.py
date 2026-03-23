from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class ApproveActionRequest(BaseModel):
    modifier: Optional[dict] = None


class RejectActionRequest(BaseModel):
    reason: Optional[str] = None


class ActionOut(BaseModel):
    id: UUID
    incident_id: UUID
    sop_step: Optional[int] = None
    tier: str
    action_type: str
    status: str
    description: str
    assigned_to: Optional[UUID] = None
    approved_by: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    detail: Optional[dict] = {}
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
