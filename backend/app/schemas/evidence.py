from pydantic import BaseModel, ConfigDict
from typing import Optional
from uuid import UUID
from datetime import datetime


class EvidenceOut(BaseModel):
    id: UUID
    incident_id: UUID
    uploaded_by: Optional[UUID] = None
    file_type: str
    file_name: str
    file_size_bytes: int
    sha256_hash: str
    duration_seconds: Optional[float] = None
    mime_type: Optional[str] = None
    meta: Optional[dict] = {}
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
