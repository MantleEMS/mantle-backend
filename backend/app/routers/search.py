from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime
import uuid

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.services.search_service import full_text_search, query_audit_log

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    from_date: Optional[datetime] = Query(default=None, alias="from"),
    to_date: Optional[datetime] = Query(default=None, alias="to"),
    emergency_type: Optional[str] = None,
    severity_min: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await full_text_search(
        db=db,
        org_id=current_user.org_id,
        q=q,
        from_date=from_date,
        to_date=to_date,
        emergency_type=emergency_type,
        severity_min=severity_min,
    )


@router.get("/audit")
async def audit_log(
    incident_id: Optional[uuid.UUID] = None,
    event_type: Optional[str] = None,
    from_date: Optional[datetime] = Query(default=None, alias="from"),
    to_date: Optional[datetime] = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await query_audit_log(
        db=db,
        org_id=current_user.org_id,
        incident_id=incident_id,
        event_type=event_type,
        from_date=from_date,
        to_date=to_date,
        page=page,
        page_size=page_size,
    )

    events = result["events"]
    return {
        "events": [
            {
                "id": str(e.id),
                "org_id": str(e.org_id) if e.org_id else None,
                "incident_id": str(e.incident_id) if e.incident_id else None,
                "event_type": e.event_type,
                "actor_type": e.actor_type,
                "actor_id": str(e.actor_id) if e.actor_id else None,
                "detail": e.detail or {},
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
        "total": result["total"],
        "page": result["page"],
        "page_size": result["page_size"],
    }
