"""
Demo seed data for Mantle EMS.
Organization: Sunrise Home Health
Creates: 1 org, 2 facilities, 7 users, 2 SOPs
"""

import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Organization, Facility, User, SOP
from app.services.auth_service import hash_password
from app.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

DEFAULT_PASSWORD = "password123"
SUPER_ADMIN_EMAIL = "admin@mantle.system"


async def run_seed():
    async with AsyncSessionLocal() as db:
        # Ensure super_admin exists (idempotent, runs every time)
        sa_result = await db.execute(select(User).where(User.email == SUPER_ADMIN_EMAIL))
        if not sa_result.scalar_one_or_none():
            super_admin = User(
                org_id=None,
                email=SUPER_ADMIN_EMAIL,
                password_hash=hash_password("superadmin123"),
                name="Platform Super Admin",
                role="super_admin",
                status="active",
                qualifications=[],
                medical_flags=[],
            )
            db.add(super_admin)
            logger.info(f"Super admin created: {SUPER_ADMIN_EMAIL}")

        # Check if Sunrise org already seeded
        result = await db.execute(select(Organization).where(Organization.slug == "sunrise"))
        org = result.scalar_one_or_none()
        if org:
            # Ensure org_admin exists for Sunrise (idempotent)
            oa_result = await db.execute(select(User).where(User.email == "orgadmin@sunrise.demo"))
            if not oa_result.scalar_one_or_none():
                db.add(User(
                    org_id=org.id,
                    email="orgadmin@sunrise.demo",
                    password_hash=hash_password(DEFAULT_PASSWORD),
                    name="Sunrise Org Admin",
                    phone="512-555-0100",
                    role="org_admin",
                    status="active",
                    qualifications=[],
                    medical_flags=[],
                ))
                logger.info("Org admin created: orgadmin@sunrise.demo")
            await db.commit()
            logger.info("Seed data already present — skipping full seed")
            return

        logger.info("Seeding demo data...")

        # --- Organization ---
        org = Organization(
            name="Sunrise Home Health",
            slug="sunrise",
            settings={"timezone": "America/Chicago"},
        )
        db.add(org)
        await db.flush()

        # --- Facilities ---
        morrison = Facility(
            org_id=org.id,
            name="Morrison Residence",
            facility_type="patient_home",
            address={
                "street": "1247 Oak St, Apt 3B",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
                "lat": 30.2672,
                "lng": -97.7431,
            },
            risk_flags=[
                {
                    "flag": "hostile_household_member",
                    "source": "intake_notes",
                    "noted_by": "intake_coordinator",
                    "detail": "Adult son was hostile during intake. Approach with caution.",
                }
            ],
            cell_coverage="good",
            nearest_hospital={
                "name": "Dell Seton Medical Center",
                "address": "1500 Red River St, Austin TX",
                "distance_mi": 1.8,
                "drive_min": 6,
            },
        )
        db.add(morrison)

        chen_facility = Facility(
            org_id=org.id,
            name="Chen Residence",
            facility_type="patient_home",
            address={
                "street": "892 Pine Ave",
                "city": "Austin",
                "state": "TX",
                "zip": "78702",
                "lat": 30.2631,
                "lng": -97.7280,
            },
            risk_flags=[],
            cell_coverage="good",
            nearest_hospital={
                "name": "St. David's Medical Center",
                "address": "919 E 32nd St, Austin TX",
                "distance_mi": 2.1,
                "drive_min": 8,
            },
        )
        db.add(chen_facility)
        await db.flush()

        # --- Users ---
        users = [
            User(
                org_id=org.id,
                email="maria@sunrise.demo",
                password_hash=hash_password(DEFAULT_PASSWORD),
                name="Maria Rodriguez",
                phone="512-555-0101",
                role="worker",
                status="on_duty",
                qualifications=["rn", "cpr"],
                medical_flags=[],
            ),
            User(
                org_id=org.id,
                email="torres@sunrise.demo",
                password_hash=hash_password(DEFAULT_PASSWORD),
                name="Cmdr. Torres",
                phone="512-555-0102",
                role="commander",
                status="on_duty",
                qualifications=["dispatcher"],
                medical_flags=[],
            ),
            User(
                org_id=org.id,
                email="sarah@sunrise.demo",
                password_hash=hash_password(DEFAULT_PASSWORD),
                name="Sarah Chen",
                phone="512-555-0103",
                role="worker",
                status="on_duty",
                qualifications=["rn", "cpr", "first_aid"],
                medical_flags=[],
            ),
            User(
                org_id=org.id,
                email="williams@sunrise.demo",
                password_hash=hash_password(DEFAULT_PASSWORD),
                name="J. Williams",
                phone="512-555-0104",
                role="worker",
                status="on_duty",
                qualifications=["cpr"],
                medical_flags=[],
            ),
            User(
                org_id=org.id,
                email="park@sunrise.demo",
                password_hash=hash_password(DEFAULT_PASSWORD),
                name="K. Park",
                phone="512-555-0105",
                role="worker",
                status="on_duty",
                qualifications=["cpr"],
                medical_flags=[],
            ),
            User(
                org_id=org.id,
                email="lee@sunrise.demo",
                password_hash=hash_password(DEFAULT_PASSWORD),
                name="D. Lee",
                phone="512-555-0106",
                role="worker",
                status="off_duty",
                qualifications=[],
                medical_flags=[],
            ),
            User(
                org_id=org.id,
                email="dpark@sunrise.demo",
                password_hash=hash_password(DEFAULT_PASSWORD),
                name="D. Park",
                phone="512-555-0107",
                role="supervisor",
                status="active",
                qualifications=["auditor"],
                medical_flags=[],
            ),
            User(
                org_id=org.id,
                email="orgadmin@sunrise.demo",
                password_hash=hash_password(DEFAULT_PASSWORD),
                name="Sunrise Org Admin",
                phone="512-555-0100",
                role="org_admin",
                status="active",
                qualifications=[],
                medical_flags=[],
            ),
        ]
        for u in users:
            db.add(u)

        # --- SOPs ---
        sop_wv = SOP(
            org_id=org.id,
            name="Workplace Violence Response",
            sop_code="SOP-WV-001",
            emergency_type="workplace_violence",
            description="Standard protocol for workplace violence emergencies involving home healthcare workers.",
            steps=[
                {
                    "step": 1,
                    "actor": "ai",
                    "action": "begin_recording",
                    "auto": True,
                    "description": "Automatically begin audio/video recording for evidence collection.",
                    "target_time_sec": 0,
                    "tier": None,
                },
                {
                    "step": 2,
                    "actor": "ai",
                    "action": "alert_commander",
                    "auto": True,
                    "description": "Alert on-duty commander with incident details and location.",
                    "target_time_sec": 5,
                    "tier": None,
                },
                {
                    "step": 3,
                    "actor": "ai",
                    "action": "retrieve_context",
                    "auto": True,
                    "description": "Retrieve facility risk flags, patient profile, and worker history.",
                    "target_time_sec": 10,
                    "tier": None,
                },
                {
                    "step": 4,
                    "actor": "commander",
                    "action": "dispatch_responder",
                    "auto": False,
                    "description": "Recommend dispatch of nearest on-duty responder with de-escalation training.",
                    "target_time_sec": 30,
                    "tier": "amber",
                },
                {
                    "step": 5,
                    "actor": "commander",
                    "action": "contact_911",
                    "auto": False,
                    "description": "Recommend contacting 911 if threat level indicates imminent danger.",
                    "target_time_sec": 60,
                    "tier": "red",
                },
                {
                    "step": 6,
                    "actor": "commander",
                    "action": "dispatch_responder",
                    "auto": False,
                    "description": "Commander confirms dispatch decision and assigns responder.",
                    "target_time_sec": 90,
                    "tier": "amber",
                },
                {
                    "step": 7,
                    "actor": "ai",
                    "action": "notify_responder",
                    "auto": True,
                    "description": "Notify dispatched responder with incident details, ETA calculation, and route.",
                    "target_time_sec": 95,
                    "tier": None,
                },
                {
                    "step": 8,
                    "actor": "ai",
                    "action": "de_escalation_guidance",
                    "auto": True,
                    "description": "Send de-escalation script and safety tips to initiating worker.",
                    "target_time_sec": 100,
                    "tier": None,
                },
                {
                    "step": 9,
                    "actor": "ai",
                    "action": "status_monitor",
                    "auto": True,
                    "description": "Monitor worker and responder GPS. Alert commander if worker goes silent.",
                    "target_time_sec": 120,
                    "tier": None,
                },
                {
                    "step": 10,
                    "actor": "commander",
                    "action": "resolve_incident",
                    "auto": False,
                    "description": "Commander confirms situation resolved and closes incident.",
                    "target_time_sec": None,
                    "tier": "green",
                },
            ],
            responder_checklist=[
                {"step": 1, "text": "Acknowledge dispatch and confirm ETA"},
                {"step": 2, "text": "Review facility risk flags before arrival"},
                {"step": 3, "text": "Contact commander on arrival"},
                {"step": 4, "text": "Assess situation — do not escalate"},
                {"step": 5, "text": "Assist worker in leaving if necessary"},
                {"step": 6, "text": "Complete incident report"},
            ],
            is_active=True,
        )
        db.add(sop_wv)

        sop_med = SOP(
            org_id=org.id,
            name="Medical Emergency Response",
            sop_code="SOP-MED-001",
            emergency_type="medical",
            description="Standard protocol for medical emergencies involving patients in home healthcare settings.",
            steps=[
                {
                    "step": 1,
                    "actor": "ai",
                    "action": "begin_recording",
                    "auto": True,
                    "description": "Begin recording for documentation and handoff to EMS.",
                    "target_time_sec": 0,
                    "tier": None,
                },
                {
                    "step": 2,
                    "actor": "ai",
                    "action": "alert_commander",
                    "auto": True,
                    "description": "Alert commander with patient info and worker location.",
                    "target_time_sec": 5,
                    "tier": None,
                },
                {
                    "step": 3,
                    "actor": "ai",
                    "action": "retrieve_patient_profile",
                    "auto": True,
                    "description": "Retrieve patient medical history, allergies, medications, and emergency contacts.",
                    "target_time_sec": 8,
                    "tier": None,
                },
                {
                    "step": 4,
                    "actor": "commander",
                    "action": "contact_911",
                    "auto": False,
                    "description": "Recommend immediate 911 call with patient medical summary.",
                    "target_time_sec": 15,
                    "tier": "red",
                },
                {
                    "step": 5,
                    "actor": "commander",
                    "action": "dispatch_responder",
                    "auto": False,
                    "description": "Recommend dispatch of nearest RN-qualified responder.",
                    "target_time_sec": 20,
                    "tier": "amber",
                },
                {
                    "step": 6,
                    "actor": "commander",
                    "action": "notify_emergency_contact",
                    "auto": False,
                    "description": "Recommend notifying patient family / emergency contact.",
                    "target_time_sec": 30,
                    "tier": "amber",
                },
                {
                    "step": 7,
                    "actor": "commander",
                    "action": "dispatch_responder",
                    "auto": False,
                    "description": "Commander confirms dispatch and assigns responder.",
                    "target_time_sec": 45,
                    "tier": "amber",
                },
                {
                    "step": 8,
                    "actor": "ai",
                    "action": "first_aid_guidance",
                    "auto": True,
                    "description": "Send step-by-step first aid instructions to worker based on reported symptoms.",
                    "target_time_sec": 50,
                    "tier": None,
                },
                {
                    "step": 9,
                    "actor": "ai",
                    "action": "ems_handoff",
                    "auto": True,
                    "description": "Prepare patient summary document for EMS handoff when they arrive.",
                    "target_time_sec": 120,
                    "tier": None,
                },
                {
                    "step": 10,
                    "actor": "commander",
                    "action": "resolve_incident",
                    "auto": False,
                    "description": "Commander closes incident after EMS handoff or situation resolved.",
                    "target_time_sec": None,
                    "tier": "green",
                },
            ],
            responder_checklist=[
                {"step": 1, "text": "Acknowledge dispatch and confirm ETA"},
                {"step": 2, "text": "Review patient medical history and allergies"},
                {"step": 3, "text": "Bring first aid kit and AED if available"},
                {"step": 4, "text": "Assess patient on arrival — vitals, consciousness"},
                {"step": 5, "text": "Begin CPR or first aid as appropriate"},
                {"step": 6, "text": "Brief EMS on arrival with patient summary"},
            ],
            is_active=True,
        )
        db.add(sop_med)

        await db.commit()
        logger.info(
            f"Seed complete: org={org.id}, "
            f"facilities=2, users={len(users)}, sops=2"
        )
        logger.info(f"Demo logins — password for all org users: '{DEFAULT_PASSWORD}'")
        logger.info(f"  {SUPER_ADMIN_EMAIL} (super_admin) password: superadmin123")
        logger.info("  orgadmin@sunrise.demo (org_admin)")
        logger.info("  torres@sunrise.demo (commander/on_duty)")
        logger.info("  dpark@sunrise.demo (supervisor)")
        logger.info("  maria@sunrise.demo (worker/on_duty)")
