import hashlib
import os
import uuid
from pathlib import Path
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Evidence
from app.services.thread_service import create_message, write_audit


async def upload_evidence(
    db: AsyncSession,
    incident_id: uuid.UUID,
    org_id: uuid.UUID,
    uploaded_by: uuid.UUID,
    file: UploadFile,
    file_type: str,
    metadata: dict = None,
) -> Evidence:
    # Read file content
    content = await file.read()

    # Compute SHA-256
    sha256 = hashlib.sha256(content).hexdigest()

    # Store file
    ext = Path(file.filename).suffix if file.filename else ""
    safe_name = f"{uuid.uuid4()}{ext}"
    incident_dir = Path(settings.UPLOADS_DIR) / str(incident_id)
    incident_dir.mkdir(parents=True, exist_ok=True)
    file_path = incident_dir / safe_name

    with open(file_path, "wb") as f:
        f.write(content)

    evidence = Evidence(
        incident_id=incident_id,
        uploaded_by=uploaded_by,
        file_type=file_type,
        file_name=file.filename or safe_name,
        file_path=str(file_path),
        file_size_bytes=len(content),
        sha256_hash=sha256,
        mime_type=file.content_type,
        meta=metadata or {},
    )
    db.add(evidence)
    await db.commit()
    await db.refresh(evidence)

    # Post evidence message to thread
    await create_message(
        db=db,
        incident_id=incident_id,
        sender_type="human",
        message_type="evidence",
        content=f"Evidence uploaded: {file.filename} ({file_type})",
        metadata={
            "evidence_id": str(evidence.id),
            "file_type": file_type,
            "sha256": sha256,
        },
        sender_id=uploaded_by,
    )

    # Write audit log
    await write_audit(
        db=db,
        org_id=org_id,
        event_type="evidence.uploaded",
        actor_type="human",
        actor_id=uploaded_by,
        incident_id=incident_id,
        detail={"evidence_id": str(evidence.id), "file_type": file_type, "sha256": sha256},
    )

    return evidence
