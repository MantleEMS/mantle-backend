from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import json
import uuid

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, Incident, Evidence
from app.schemas.evidence import EvidenceOut
from app.services.evidence_service import upload_evidence

router = APIRouter(prefix="/api/v1/incidents", tags=["evidence"])

ALLOWED_FILE_TYPES = {"photo", "audio", "video", "document"}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


@router.post("/{incident_id}/evidence", response_model=EvidenceOut, status_code=201)
async def upload(
    incident_id: uuid.UUID,
    file: UploadFile = File(...),
    file_type: str = Form(...),
    metadata: str = Form(default="{}"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if file_type not in ALLOWED_FILE_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid file_type. Must be one of: {ALLOWED_FILE_TYPES}")

    # Verify incident access
    inc_result = await db.execute(
        select(Incident).where(
            and_(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        )
    )
    incident = inc_result.scalar_one_or_none()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Parse metadata JSON
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid metadata JSON")

    evidence = await upload_evidence(
        db=db,
        incident_id=incident_id,
        org_id=current_user.org_id,
        uploaded_by=current_user.id,
        file=file,
        file_type=file_type,
        metadata=meta,
    )
    return evidence


@router.get("/{incident_id}/evidence", response_model=list[EvidenceOut])
async def list_evidence(
    incident_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    inc_result = await db.execute(
        select(Incident).where(
            and_(Incident.id == incident_id, Incident.org_id == current_user.org_id)
        )
    )
    if not inc_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Incident not found")

    result = await db.execute(
        select(Evidence)
        .where(Evidence.incident_id == incident_id)
        .order_by(Evidence.created_at.asc())
    )
    return result.scalars().all()
