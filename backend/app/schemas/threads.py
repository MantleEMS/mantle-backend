from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from uuid import UUID
from datetime import datetime


class PostMessageRequest(BaseModel):
    message_type: str  # text, status_update, etc.
    content: str
    metadata: Optional[dict] = {}


class MessageOut(BaseModel):
    id: UUID
    incident_id: UUID
    sender_id: Optional[UUID] = None
    sender_type: str
    message_type: str
    content: str
    meta: Optional[dict] = {}
    seq: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
