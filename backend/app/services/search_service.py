from uuid import UUID
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, text

from app.models import Incident, Message, AuditLog


async def full_text_search(
    db: AsyncSession,
    org_id: UUID,
    q: str,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    emergency_type: Optional[str] = None,
    severity_min: Optional[int] = None,
) -> dict:
    # Build incident filter
    incident_filters = [Incident.org_id == org_id]
    if from_date:
        incident_filters.append(Incident.initiated_at >= from_date)
    if to_date:
        incident_filters.append(Incident.initiated_at <= to_date)
    if emergency_type:
        incident_filters.append(Incident.emergency_type == emergency_type)
    if severity_min:
        incident_filters.append(Incident.severity >= severity_min)

    incidents_result = await db.execute(
        select(Incident).where(and_(*incident_filters))
    )
    incidents = incidents_result.scalars().all()
    incident_ids = [i.id for i in incidents]

    if not incident_ids:
        return {"results": [], "total": 0}

    # Search messages for matching content (simple ILIKE for PoC)
    messages_result = await db.execute(
        select(Message).where(
            and_(
                Message.incident_id.in_(incident_ids),
                Message.content.ilike(f"%{q}%"),
            )
        ).limit(500)
    )
    messages = messages_result.scalars().all()

    # Group matches by incident
    incident_map = {i.id: i for i in incidents}
    matches_by_incident: dict[UUID, list] = {}
    for msg in messages:
        if msg.incident_id not in matches_by_incident:
            matches_by_incident[msg.incident_id] = []
        # Simple snippet: first 200 chars around match
        content_lower = msg.content.lower()
        q_lower = q.lower()
        idx = content_lower.find(q_lower)
        start = max(0, idx - 80)
        end = min(len(msg.content), idx + len(q) + 80)
        snippet = msg.content[start:end]
        matches_by_incident[msg.incident_id].append({
            "message_id": str(msg.id),
            "snippet": snippet,
            "highlight": q,
            "seq": msg.seq,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        })

    results = []
    for incident_id, match_list in matches_by_incident.items():
        inc = incident_map.get(incident_id)
        if inc:
            results.append({
                "incident_id": str(inc.id),
                "incident_number": inc.incident_number,
                "emergency_type": inc.emergency_type,
                "status": inc.status,
                "initiated_at": inc.initiated_at.isoformat() if inc.initiated_at else None,
                "matches": match_list,
            })

    return {"results": results, "total": len(results)}


async def query_audit_log(
    db: AsyncSession,
    org_id: Optional[UUID] = None,
    incident_id: Optional[UUID] = None,
    event_type: Optional[str] = None,
    from_date: Optional[datetime] = None,
    to_date: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    filters = []
    if org_id:
        filters.append(AuditLog.org_id == org_id)
    if incident_id:
        filters.append(AuditLog.incident_id == incident_id)
    if event_type:
        filters.append(AuditLog.event_type == event_type)
    if from_date:
        filters.append(AuditLog.created_at >= from_date)
    if to_date:
        filters.append(AuditLog.created_at <= to_date)

    count_result = await db.execute(
        select(func.count(AuditLog.id)).where(and_(*filters) if filters else True)
    )
    total = count_result.scalar() or 0

    offset = (page - 1) * page_size
    result = await db.execute(
        select(AuditLog)
        .where(and_(*filters) if filters else True)
        .order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    events = result.scalars().all()

    return {
        "events": events,
        "total": total,
        "page": page,
        "page_size": page_size,
    }
