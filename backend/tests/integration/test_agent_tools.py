"""
Integration tests for agent data and action tools against a real DB.
"""

import pytest
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Message, Action, AuditLog
from app.tools.data_tools import (
    get_incident_details,
    get_worker_profile,
    get_facility_info,
    get_available_responders,
    get_sop,
    get_incident_history,
    get_patient_info,
)
from app.tools.action_tools import (
    start_evidence_collection,
    post_thread_message,
    create_pending_action,
    update_incident,
)
from app.tools.adaptive_tools import (
    propose_step_adaptation,
    propose_sop_switch,
)
from app.services.incident_service import create_incident


pytestmark = pytest.mark.integration


# ── Data tools ────────────────────────────────────────────────────────────────

async def test_get_worker_profile_real(db: AsyncSession, seed):
    result = await get_worker_profile(db, str(seed["worker"].id))
    assert result["name"] == "Test Worker"
    assert "rn" in result["qualifications"]
    assert result["role"] == "worker"


async def test_get_worker_profile_missing(db: AsyncSession, seed):
    result = await get_worker_profile(db, str(uuid.uuid4()))
    assert "error" in result


async def test_get_facility_info_real(db: AsyncSession, seed):
    result = await get_facility_info(db, str(seed["facility"].id))
    assert result["name"] == "Test Facility"
    assert result["cell_coverage"] == "good"


async def test_get_sop_real(db: AsyncSession, seed):
    result = await get_sop(db, str(seed["org"].id), "workplace_violence")
    assert result["emergency_type"] == "workplace_violence"
    assert len(result["steps"]) > 0


async def test_get_sop_not_found(db: AsyncSession, seed):
    result = await get_sop(db, str(seed["org"].id), "unknown_type")
    assert "error" in result


async def test_get_available_responders_real(db: AsyncSession, seed):
    result = await get_available_responders(
        db, str(seed["org"].id), 30.27, -97.74
    )
    # Worker has on_duty status and a location
    assert len(result["responders"]) >= 1
    names = [r["name"] for r in result["responders"]]
    assert "Test Worker" in names


async def test_get_available_responders_qualification_filter(db: AsyncSession, seed):
    result = await get_available_responders(
        db, str(seed["org"].id), 30.27, -97.74,
        qualification_filter=["rn"]
    )
    for r in result["responders"]:
        assert "rn" in r["qualifications"]


async def test_get_incident_details_real(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
    )
    result = await get_incident_details(db, str(incident.id))
    assert result["id"] == str(incident.id)
    assert result["emergency_type"] == "medical"
    assert result["status"] == "triggered"


async def test_get_incident_history_real(db: AsyncSession, seed):
    # Create an incident at the facility
    await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
        facility_id=seed["facility"].id,
    )
    result = await get_incident_history(db, str(seed["facility"].id))
    assert len(result["history"]) >= 1
    assert result["history"][0]["emergency_type"] == "workplace_violence"


async def test_get_patient_info_active_incident(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
        facility_id=seed["facility"].id,
        patient_info={"name": "John Doe", "allergies": ["penicillin"]},
    )
    result = await get_patient_info(db, str(seed["facility"].id))
    assert result["patient_info"]["name"] == "John Doe"


# ── Action tools ──────────────────────────────────────────────────────────────

async def test_start_evidence_collection_real(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    result = await start_evidence_collection(db, str(incident.id), ["audio", "gps"])
    assert result["status"] == "started"

    # Message should be in DB
    msg_result = await db.execute(
        select(Message).where(
            Message.incident_id == incident.id,
            Message.message_type == "system_event",
        )
    )
    messages = msg_result.scalars().all()
    assert any("recording" in m.content.lower() for m in messages)


async def test_post_thread_message_real(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
    )
    result = await post_thread_message(db, str(incident.id), "SOP started.", "system_event")
    assert result["status"] == "posted"
    assert result["seq"] >= 1


async def test_create_pending_action_real(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    result = await create_pending_action(
        db, str(incident.id), "red", "dispatch_responder",
        "Dispatch nearest responder"
    )
    assert result["status"] == "pending"
    assert result["tier"] == "red"

    # Action should be in DB
    action_result = await db.execute(
        select(Action).where(Action.incident_id == incident.id)
    )
    actions = action_result.scalars().all()
    assert len(actions) >= 1
    assert actions[-1].action_type == "dispatch_responder"


async def test_update_incident_real(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    result = await update_incident(db, str(incident.id), {"severity": 5})
    assert result["status"] == "updated"
    assert result["fields"]["severity"] == 5

    # Verify in DB
    from sqlalchemy import select
    from app.models import Incident
    refreshed = await db.execute(select(Incident).where(Incident.id == incident.id))
    inc = refreshed.scalar_one()
    assert inc.severity == 5


# ── Adaptive tools ─────────────────────────────────────────────────────────────

async def test_propose_step_adaptation_creates_amber_action(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
    )
    result = await propose_step_adaptation(
        db=db,
        incident_id=str(incident.id),
        step_number=2,
        adaptation_type="skip",
        reason="Patient has DNR order on file",
        proposed_description="Skip CPR — DNR documented in patient record",
    )
    assert result["status"] == "proposed"
    assert result["awaiting_commander_approval"] is True
    assert result["step_number"] == 2
    assert result["adaptation_type"] == "skip"

    # Action in DB with amber tier
    action_result = await db.execute(
        select(Action).where(Action.incident_id == incident.id)
    )
    actions = action_result.scalars().all()
    amber_actions = [a for a in actions if a.tier == "amber" and a.action_type == "sop_adaptation"]
    assert len(amber_actions) == 1
    assert "DNR" in amber_actions[0].description


async def test_propose_step_adaptation_posts_thread_message(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
    )
    await propose_step_adaptation(
        db=db,
        incident_id=str(incident.id),
        step_number=1,
        adaptation_type="modify",
        reason="Poor cell coverage at facility — GPS unreliable",
        proposed_description="Use satellite GPS fallback for location tracking",
    )
    msg_result = await db.execute(
        select(Message).where(
            Message.incident_id == incident.id,
            Message.message_type == "classification",
        )
    )
    messages = msg_result.scalars().all()
    assert any("adaptation" in m.content.lower() for m in messages)
    assert any("approval" in m.content.lower() for m in messages)


async def test_propose_step_adaptation_writes_audit(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
    )
    await propose_step_adaptation(
        db=db,
        incident_id=str(incident.id),
        step_number=3,
        adaptation_type="add_after",
        reason="Patient on anticoagulants — bleeding risk requires extra step",
        proposed_description="Apply pressure bandage before transport",
    )
    audit_result = await db.execute(
        select(AuditLog).where(AuditLog.incident_id == incident.id)
    )
    entries = audit_result.scalars().all()
    assert any(e.event_type == "sop.adaptation_proposed" for e in entries)


async def test_propose_step_adaptation_unknown_incident(db: AsyncSession, seed):
    result = await propose_step_adaptation(
        db=db,
        incident_id=str(uuid.uuid4()),
        step_number=1,
        adaptation_type="skip",
        reason="test",
        proposed_description="test",
    )
    assert "error" in result


async def test_propose_sop_switch_creates_amber_action(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="panic_button",
    )
    result = await propose_sop_switch(
        db=db,
        incident_id=str(incident.id),
        current_sop_code="SOP-MED-TEST",
        recommended_sop_code="SOP-WV-TEST",
        reason="Panic button triggered during home visit — worker reports aggressor on-site",
    )
    assert result["status"] == "proposed"
    assert result["awaiting_commander_approval"] is True
    assert result["current_sop_code"] == "SOP-MED-TEST"
    assert result["recommended_sop_code"] == "SOP-WV-TEST"
    # Should resolve the SOP name
    assert result["recommended_sop_name"] == "Workplace Violence SOP"

    action_result = await db.execute(
        select(Action).where(Action.incident_id == incident.id)
    )
    actions = action_result.scalars().all()
    switch_actions = [a for a in actions if a.action_type == "sop_switch"]
    assert len(switch_actions) == 1
    assert switch_actions[0].tier == "amber"


async def test_propose_sop_switch_unknown_recommended_sop(db: AsyncSession, seed):
    """Falls back gracefully when recommended SOP doesn't exist."""
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="voice",
    )
    result = await propose_sop_switch(
        db=db,
        incident_id=str(incident.id),
        current_sop_code="SOP-MED-TEST",
        recommended_sop_code="SOP-NONEXISTENT",
        reason="test reason",
    )
    # Still proposes — uses code as name fallback
    assert result["status"] == "proposed"
    assert result["recommended_sop_name"] == "SOP-NONEXISTENT"


async def test_propose_sop_switch_posts_thread_message(db: AsyncSession, seed):
    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="medical",
        trigger_source="panic_button",
    )
    await propose_sop_switch(
        db=db,
        incident_id=str(incident.id),
        current_sop_code="SOP-MED-TEST",
        recommended_sop_code="SOP-WV-TEST",
        reason="Context suggests violence not medical emergency",
    )
    msg_result = await db.execute(
        select(Message).where(
            Message.incident_id == incident.id,
            Message.message_type == "classification",
        )
    )
    messages = msg_result.scalars().all()
    assert any("mismatch" in m.content.lower() or "switch" in m.content.lower() for m in messages)


async def test_propose_sop_switch_unknown_incident(db: AsyncSession, seed):
    result = await propose_sop_switch(
        db=db,
        incident_id=str(uuid.uuid4()),
        current_sop_code="SOP-MED-TEST",
        recommended_sop_code="SOP-WV-TEST",
        reason="test",
    )
    assert "error" in result
