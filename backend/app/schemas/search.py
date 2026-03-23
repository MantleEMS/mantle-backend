from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class SearchResult(BaseModel):
    incident_id: UUID
    incident_number: str
    emergency_type: str
    status: str
    initiated_at: datetime
    matches: List[dict]


class SearchResponse(BaseModel):
    results: List[SearchResult]
    total: int


class AuditEventOut(BaseModel):
    id: UUID
    org_id: Optional[UUID] = None
    incident_id: Optional[UUID] = None
    event_type: str
    actor_type: str
    actor_id: Optional[UUID] = None
    detail: Optional[dict] = {}
    created_at: datetime

    class Config:
        from_attributes = True


class AuditResponse(BaseModel):
    events: List[AuditEventOut]
    total: int
    page: int
    page_size: int
