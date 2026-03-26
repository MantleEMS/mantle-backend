"""
Read-only data tools. The LLM calls these to gather context before making recommendations.
No side effects — safe to call at any time.
"""

import math
import logging
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_

from app.models import Incident, User, Facility, SOP, AuditLog
from app.tools.registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two GPS coordinates."""
    R = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ── Tool handlers ──────────────────────────────────────────────────────────────

async def get_incident_details(db: AsyncSession, incident_id: str) -> dict:
    """Get full incident record."""
    result = await db.execute(select(Incident).where(Incident.id == UUID(incident_id)))
    inc = result.scalar_one_or_none()
    if not inc:
        return {"error": "Incident not found"}
    return {
        "id": str(inc.id),
        "incident_number": inc.incident_number,
        "status": inc.status,
        "emergency_type": inc.emergency_type,
        "trigger_source": inc.trigger_source,
        "severity": inc.severity,
        "facility_id": str(inc.facility_id) if inc.facility_id else None,
        "commander_id": str(inc.commander_id) if inc.commander_id else None,
        "initiated_by": str(inc.initiated_by),
        "location": inc.location or {},
        "patient_info": inc.patient_info or {},
        "initiated_at": inc.initiated_at.isoformat() if inc.initiated_at else None,
    }


async def get_worker_profile(db: AsyncSession, user_id: str) -> dict:
    """Get worker name, role, qualifications, medical flags, last location, phone."""
    result = await db.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        return {"error": "User not found"}
    return {
        "id": str(user.id),
        "name": user.name,
        "roles": user.roles,
        "phone": user.phone,
        "status": user.status,
        "qualifications": user.qualifications or [],
        "medical_flags": user.medical_flags or [],
        "last_location": user.last_location or {},
    }


async def get_facility_info(db: AsyncSession, facility_id: str) -> dict:
    """Get facility address, risk flags, cell coverage, nearest hospital, notes."""
    result = await db.execute(select(Facility).where(Facility.id == UUID(facility_id)))
    fac = result.scalar_one_or_none()
    if not fac:
        return {"error": "Facility not found"}
    return {
        "id": str(fac.id),
        "name": fac.name,
        "facility_type": fac.facility_type,
        "address": fac.address or {},
        "risk_flags": fac.risk_flags or [],
        "cell_coverage": fac.cell_coverage,
        "nearest_hospital": fac.nearest_hospital or {},
        "notes": fac.notes,
    }


async def get_patient_info(db: AsyncSession, facility_id: str) -> dict:
    """
    Get patient info for the current patient at this facility.
    Pulls from the most recent active incident's patient_info field.
    """
    result = await db.execute(
        select(Incident)
        .where(
            and_(
                Incident.facility_id == UUID(facility_id),
                Incident.status.in_(["triggered", "active"]),
            )
        )
        .order_by(Incident.initiated_at.desc())
        .limit(1)
    )
    inc = result.scalar_one_or_none()
    if not inc or not inc.patient_info:
        return {"patient_info": None, "note": "No active incident or patient info at this facility"}
    return {"patient_info": inc.patient_info}


async def get_available_responders(
    db: AsyncSession,
    org_id: str,
    lat: float,
    lng: float,
    qualification_filter: list[str] | None = None,
) -> dict:
    """Find on-duty responders near a location, ranked by distance."""
    query = select(User).where(
        and_(
            User.org_id == UUID(org_id),
            User.status == "on_duty",
            or_(User.roles.contains(["worker"]), User.roles.contains(["responder"])),
        )
    )
    result = await db.execute(query)
    users = result.scalars().all()

    ranked = []
    for user in users:
        if qualification_filter:
            user_quals = set(user.qualifications or [])
            if not all(q in user_quals for q in qualification_filter):
                continue

        if user.last_location and user.last_location.get("lat") and user.last_location.get("lng"):
            dist = _haversine(lat, lng, user.last_location["lat"], user.last_location["lng"])
            eta_min = (dist / 30) * 60  # ~30 km/h avg driving speed
        else:
            dist = 999.0
            eta_min = 999.0

        ranked.append({
            "user_id": str(user.id),
            "name": user.name,
            "qualifications": user.qualifications or [],
            "distance_km": round(dist, 2),
            "eta_minutes": round(eta_min, 1),
        })

    ranked.sort(key=lambda r: r["distance_km"])
    return {"responders": ranked}


async def get_sop(db: AsyncSession, org_id: str, emergency_type: str) -> dict:
    """Get the SOP definition for this emergency type."""
    result = await db.execute(
        select(SOP).where(
            and_(
                SOP.org_id == UUID(org_id),
                SOP.emergency_type == emergency_type,
                SOP.is_active == True,
            )
        ).limit(1)
    )
    sop = result.scalar_one_or_none()
    if not sop:
        return {"error": f"No active SOP found for emergency_type={emergency_type}"}
    return {
        "id": str(sop.id),
        "name": sop.name,
        "sop_code": sop.sop_code,
        "emergency_type": sop.emergency_type,
        "description": sop.description,
        "steps": sop.steps or [],
        "responder_checklist": sop.responder_checklist or [],
    }


async def get_incident_history(db: AsyncSession, facility_id: str) -> dict:
    """Get prior incidents at this facility."""
    result = await db.execute(
        select(Incident)
        .where(Incident.facility_id == UUID(facility_id))
        .order_by(Incident.initiated_at.desc())
        .limit(20)
    )
    incidents = result.scalars().all()
    return {
        "history": [
            {
                "id": str(i.id),
                "incident_number": i.incident_number,
                "date": i.initiated_at.isoformat() if i.initiated_at else None,
                "emergency_type": i.emergency_type,
                "severity": i.severity,
                "status": i.status,
            }
            for i in incidents
        ]
    }


# ── Registration ───────────────────────────────────────────────────────────────

def register_data_tools(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="get_incident_details",
        description="Get full details of an incident including status, emergency type, trigger source, location, and patient info.",
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
            },
            "required": ["incident_id"],
        },
        handler=get_incident_details,
        category="data",
    ))

    registry.register(ToolDefinition(
        name="get_worker_profile",
        description="Get worker name, role, qualifications, medical flags, last known location, and phone number.",
        parameters={
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "UUID of the worker/user"},
            },
            "required": ["user_id"],
        },
        handler=get_worker_profile,
        category="data",
    ))

    registry.register(ToolDefinition(
        name="get_facility_info",
        description="Get facility address, risk flags, cell coverage quality, nearest hospital, and worker notes.",
        parameters={
            "type": "object",
            "properties": {
                "facility_id": {"type": "string", "description": "UUID of the facility"},
            },
            "required": ["facility_id"],
        },
        handler=get_facility_info,
        category="data",
    ))

    registry.register(ToolDefinition(
        name="get_patient_info",
        description="Get patient name, known conditions, allergies, medications, and emergency contact. Use for medical emergencies only — do NOT call for workplace violence incidents.",
        parameters={
            "type": "object",
            "properties": {
                "facility_id": {"type": "string", "description": "UUID of the facility where the patient resides"},
            },
            "required": ["facility_id"],
        },
        handler=get_patient_info,
        category="data",
    ))

    registry.register(ToolDefinition(
        name="get_available_responders",
        description="Find on-duty responders near a location, ranked by distance and ETA. Optionally filter by qualifications (e.g. rn, cpr, first_aid).",
        parameters={
            "type": "object",
            "properties": {
                "org_id": {"type": "string", "description": "UUID of the organization"},
                "lat": {"type": "number", "description": "Latitude of the incident location"},
                "lng": {"type": "number", "description": "Longitude of the incident location"},
                "qualification_filter": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of required qualifications (e.g. ['rn', 'cpr'])",
                },
            },
            "required": ["org_id", "lat", "lng"],
        },
        handler=get_available_responders,
        category="data",
    ))

    registry.register(ToolDefinition(
        name="get_sop",
        description="Load the Standard Operating Procedure playbook for a given emergency type.",
        parameters={
            "type": "object",
            "properties": {
                "org_id": {"type": "string", "description": "UUID of the organization"},
                "emergency_type": {"type": "string", "description": "Emergency type: workplace_violence, medical, other, generic"},
            },
            "required": ["org_id", "emergency_type"],
        },
        handler=get_sop,
        category="data",
    ))

    registry.register(ToolDefinition(
        name="get_incident_history",
        description="Get prior incidents at this facility to check for patterns (e.g. violence history, repeat medical events).",
        parameters={
            "type": "object",
            "properties": {
                "facility_id": {"type": "string", "description": "UUID of the facility"},
            },
            "required": ["facility_id"],
        },
        handler=get_incident_history,
        category="data",
    ))
