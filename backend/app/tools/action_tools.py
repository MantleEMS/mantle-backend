"""
Action tools — write operations with side effects.
Green-tier tools (start_evidence_collection, alert_commander, post_thread_message,
update_incident) the LLM may call directly.

Red-tier tools (dispatch_responder, initiate_911_call, notify_emergency_contact)
REQUIRE commander approval. The LLM must call create_pending_action instead.
These functions are also used by the approval flow after human sign-off.
"""

import logging
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Incident, User, Participant
from app.services.thread_service import create_message, write_audit
from app.services.action_service import create_action
from app.tools.registry import ToolDefinition, ToolRegistry

logger = logging.getLogger(__name__)


# ── Tool handlers ──────────────────────────────────────────────────────────────

async def start_evidence_collection(
    db: AsyncSession,
    incident_id: str,
    media_types: list[str],
) -> dict:
    """Sends command to worker's device to begin recording. Posts system message to thread."""
    await create_message(
        db=db,
        incident_id=UUID(incident_id),
        sender_type="ai",
        message_type="system_event",
        content=f"Evidence collection started: {', '.join(media_types)}. Recording in progress.",
        metadata={"event": "recording.started", "media_types": media_types},
    )
    await write_audit(
        db=db,
        org_id=None,
        event_type="evidence.collection_started",
        actor_type="ai",
        incident_id=UUID(incident_id),
        detail={"media_types": media_types},
    )
    return {"status": "started", "media_types": media_types}


async def alert_commander(
    db: AsyncSession,
    incident_id: str,
    commander_id: str,
    priority: str = "high",
) -> dict:
    """Sends push notification to commander and creates a dashboard alert."""
    result = await db.execute(select(Incident).where(Incident.id == UUID(incident_id)))
    incident = result.scalar_one_or_none()

    if incident and incident.commander_id:
        from app.notifications.push import send_push_notification
        await send_push_notification(
            db=db,
            user_id=incident.commander_id,
            title=f"SOS: {incident.emergency_type.replace('_', ' ').title()}",
            body=f"Incident {incident.incident_number} requires your attention.",
            data={"deep_link": f"mantle://incident/{incident.id}/commander"},
        )

    await create_message(
        db=db,
        incident_id=UUID(incident_id),
        sender_type="ai",
        message_type="system_event",
        content=f"Commander alerted (priority: {priority}). Awaiting response.",
        metadata={"event": "commander.alerted", "commander_id": commander_id, "priority": priority},
    )
    return {"status": "alerted", "commander_id": commander_id, "priority": priority}


async def create_pending_action(
    db: AsyncSession,
    incident_id: str,
    tier: str,
    action_type: str,
    description: str,
    assigned_to: str | None = None,
) -> dict:
    """
    Creates a pending action card for the commander dashboard.
    This IS the approval request — use this for all red/amber tier actions.
    The commander must approve before dispatch_responder, initiate_911_call,
    or notify_emergency_contact are executed.
    """
    result = await db.execute(select(Incident).where(Incident.id == UUID(incident_id)))
    incident = result.scalar_one_or_none()
    org_id = incident.org_id if incident else None

    action = await create_action(
        db=db,
        incident_id=UUID(incident_id),
        org_id=org_id,
        action_type=action_type,
        description=description,
        tier=tier,
        assigned_to=UUID(assigned_to) if assigned_to else None,
    )
    return {
        "status": "pending",
        "action_id": str(action.id),
        "tier": tier,
        "action_type": action_type,
        "description": description,
    }


async def post_thread_message(
    db: AsyncSession,
    incident_id: str,
    content: str,
    message_type: str = "text",
    metadata: dict | None = None,
) -> dict:
    """Post a message to the incident thread. Broadcasts to all WebSocket clients."""
    msg = await create_message(
        db=db,
        incident_id=UUID(incident_id),
        sender_type="ai",
        message_type=message_type,
        content=content,
        metadata=metadata or {},
    )
    return {"status": "posted", "message_id": str(msg.id), "seq": msg.seq}


async def update_incident(
    db: AsyncSession,
    incident_id: str,
    fields: dict,
) -> dict:
    """Update incident fields (severity, classification, ai_assessment, etc.)."""
    result = await db.execute(select(Incident).where(Incident.id == UUID(incident_id)))
    incident = result.scalar_one_or_none()
    if not incident:
        return {"error": "Incident not found"}

    allowed = {"severity", "emergency_type", "ai_assessment"}
    updated = {}
    for key, value in fields.items():
        if key in allowed:
            setattr(incident, key, value)
            updated[key] = value

    if updated:
        await db.commit()
        await create_message(
            db=db,
            incident_id=UUID(incident_id),
            sender_type="ai",
            message_type="system_event",
            content=f"Incident updated: {', '.join(f'{k}={v}' for k, v in updated.items())}",
            metadata={"event": "incident.updated", "fields": updated},
        )

    return {"status": "updated", "fields": updated}


async def dispatch_responder(
    db: AsyncSession,
    incident_id: str,
    responder_id: str,
) -> dict:
    """
    APPROVAL REQUIRED — only call after commander approves a pending action.
    Sends push notification to responder and creates participant record.
    """
    result = await db.execute(select(User).where(User.id == UUID(responder_id)))
    responder = result.scalar_one_or_none()
    if not responder:
        return {"error": "Responder not found"}

    # Add as participant if not already present
    existing = await db.execute(
        select(Participant).where(
            Participant.incident_id == UUID(incident_id),
            Participant.user_id == UUID(responder_id),
        )
    )
    if not existing.scalar_one_or_none():
        db.add(Participant(
            incident_id=UUID(incident_id),
            user_id=UUID(responder_id),
            role="responder",
            name=responder.name,
            is_ai=False,
            dispatch_status="pending",
        ))
        await db.commit()

    from app.notifications.push import send_push_notification
    await send_push_notification(
        db=db,
        user_id=UUID(responder_id),
        title="Dispatch Request",
        body="You have been dispatched to an active incident.",
        data={"deep_link": f"mantle://incident/{incident_id}/responder"},
    )

    await create_message(
        db=db,
        incident_id=UUID(incident_id),
        sender_type="ai",
        message_type="action",
        content=f"Responder {responder.name} dispatched.",
        metadata={"event": "responder.dispatched", "responder_id": responder_id},
    )
    await write_audit(
        db=db,
        org_id=None,
        event_type="dispatch.sent",
        actor_type="ai",
        incident_id=UUID(incident_id),
        detail={"responder_id": responder_id, "responder_name": responder.name},
    )
    return {"status": "dispatched", "responder_id": responder_id, "responder_name": responder.name}


async def initiate_911_call(
    db: AsyncSession,
    incident_id: str,
    call_type: str,
    data_package: dict | None = None,
) -> dict:
    """
    APPROVAL REQUIRED — only call after commander approves a pending action.
    Logs 911 contact to audit trail. In production: transmits to 911 gateway. In demo: simulated.
    call_type: 'medical' | 'police'
    """
    await write_audit(
        db=db,
        org_id=None,
        event_type="911.contacted",
        actor_type="ai",
        incident_id=UUID(incident_id),
        detail={"call_type": call_type, "data_package": data_package or {}, "simulated": True},
    )
    await create_message(
        db=db,
        incident_id=UUID(incident_id),
        sender_type="ai",
        message_type="action",
        content=f"911 contacted ({call_type}). Emergency services notified.",
        metadata={"event": "911.contacted", "call_type": call_type, "simulated": True},
    )
    return {"status": "contacted", "call_type": call_type, "simulated": True}


async def notify_emergency_contact(
    db: AsyncSession,
    incident_id: str,
    contact_info: dict,
    message: str,
) -> dict:
    """
    APPROVAL REQUIRED — only call after commander approves a pending action.
    Sends SMS/call to patient's emergency contact. In demo: simulated.
    """
    await write_audit(
        db=db,
        org_id=None,
        event_type="emergency_contact.notified",
        actor_type="ai",
        incident_id=UUID(incident_id),
        detail={"contact_info": contact_info, "message": message, "simulated": True},
    )
    await create_message(
        db=db,
        incident_id=UUID(incident_id),
        sender_type="ai",
        message_type="action",
        content=f"Emergency contact notified: {contact_info.get('name', 'Unknown')}.",
        metadata={"event": "emergency_contact.notified", "contact_info": contact_info, "simulated": True},
    )
    return {"status": "notified", "contact": contact_info.get("name"), "simulated": True}


# ── Registration ───────────────────────────────────────────────────────────────

def register_action_tools(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="start_evidence_collection",
        description="Send command to worker's device to begin recording (audio, GPS, video). Posts system message to thread. Green tier — call automatically.",
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "media_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Types to record: audio, gps, video",
                },
            },
            "required": ["incident_id", "media_types"],
        },
        handler=start_evidence_collection,
        category="action",
    ))

    registry.register(ToolDefinition(
        name="alert_commander",
        description="Send push notification to the incident commander and create a dashboard alert. Green tier — call automatically.",
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "commander_id": {"type": "string", "description": "UUID of the commander to alert"},
                "priority": {"type": "string", "enum": ["high", "critical"], "description": "Alert priority"},
            },
            "required": ["incident_id", "commander_id", "priority"],
        },
        handler=alert_commander,
        category="action",
    ))

    registry.register(ToolDefinition(
        name="create_pending_action",
        description=(
            "Create a pending action card for the commander's decision dashboard. "
            "ALWAYS use this for red-tier actions: dispatch_responder, initiate_911_call, notify_emergency_contact. "
            "Never call those tools directly — create a pending action and wait for commander approval."
        ),
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "tier": {"type": "string", "enum": ["red", "amber", "green"], "description": "Action urgency tier"},
                "action_type": {
                    "type": "string",
                    "enum": ["dispatch_responder", "contact_911", "notify_emergency_contact", "resolve_incident"],
                    "description": "Type of action to be approved",
                },
                "description": {"type": "string", "description": "Human-readable description shown on the decision card"},
                "assigned_to": {"type": "string", "description": "Optional: UUID of the user this action targets"},
            },
            "required": ["incident_id", "tier", "action_type", "description"],
        },
        handler=create_pending_action,
        category="action",
    ))

    registry.register(ToolDefinition(
        name="post_thread_message",
        description="Post an informational message to the incident thread. All participants and the commander will see it in real time.",
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "content": {"type": "string", "description": "Message text — keep it concise, professional, and calming"},
                "message_type": {
                    "type": "string",
                    "enum": ["text", "system_event", "classification", "action", "status_update"],
                    "description": "Type of message",
                },
            },
            "required": ["incident_id", "content", "message_type"],
        },
        handler=post_thread_message,
        category="action",
    ))

    registry.register(ToolDefinition(
        name="update_incident",
        description="Update incident fields such as severity or ai_assessment. Posts a system event to the thread.",
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "fields": {
                    "type": "object",
                    "description": "Fields to update: severity (1-5), emergency_type, ai_assessment (object)",
                },
            },
            "required": ["incident_id", "fields"],
        },
        handler=update_incident,
        category="action",
    ))

    registry.register(ToolDefinition(
        name="dispatch_responder",
        description="APPROVAL REQUIRED — only call after commander approves. Sends push notification to responder and creates participant record. Use create_pending_action(action_type='dispatch_responder') instead of calling this directly.",
        llm_visible=False,
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "responder_id": {"type": "string", "description": "UUID of the responder to dispatch"},
            },
            "required": ["incident_id", "responder_id"],
        },
        handler=dispatch_responder,
        category="action",
    ))

    registry.register(ToolDefinition(
        name="initiate_911_call",
        description="APPROVAL REQUIRED — only call after commander approves. Logs 911 contact and transmits data package. Use create_pending_action(action_type='contact_911') instead of calling this directly.",
        llm_visible=False,
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "call_type": {"type": "string", "enum": ["medical", "police"], "description": "Type of 911 call"},
                "data_package": {"type": "object", "description": "Data to transmit to 911 (location, patient info, etc.)"},
            },
            "required": ["incident_id", "call_type"],
        },
        handler=initiate_911_call,
        category="action",
    ))

    registry.register(ToolDefinition(
        name="notify_emergency_contact",
        description="APPROVAL REQUIRED — only call after commander approves. Sends SMS/call to patient's emergency contact. Use create_pending_action(action_type='notify_emergency_contact') instead of calling this directly.",
        llm_visible=False,
        parameters={
            "type": "object",
            "properties": {
                "incident_id": {"type": "string", "description": "UUID of the incident"},
                "contact_info": {
                    "type": "object",
                    "description": "Contact details: {name, phone, relationship}",
                },
                "message": {"type": "string", "description": "Message to send to the emergency contact"},
            },
            "required": ["incident_id", "contact_info", "message"],
        },
        handler=notify_emergency_contact,
        category="action",
    ))
