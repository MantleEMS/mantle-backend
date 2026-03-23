"""
End-to-end test: LLM agent against real Ollama + real PostgreSQL.

What this tests:
  - LLMAgent.execute() drives the full agent loop via Ollama
  - Tool calls hit the real DB (get_patient_info, get_facility_info, etc.)
  - Actions, messages, and audit entries are written correctly
  - The agent finishes within the configured timeout

Prerequisites:
  - docker compose up -d postgres redis
  - Ollama running with model pulled

Run inside Docker (all hostnames resolve correctly):
  docker compose run --rm api pytest -m e2e -v -s --log-cli-level=INFO
"""

import json
import time
import uuid
import asyncio
import pytest
import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select, text

from app.config import settings
from app.models import Incident, Message, Action, AuditLog, Organization, Facility, User, SOP
from app.agent.llm_client import LLMClient, LLMConfig
from app.agent.llm_agent import LLMAgent
from app.services.incident_service import create_incident
from app.services.auth_service import hash_password
from app.tools.registry import build_registry
from tests.integration.conftest import TEST_DB_URL


pytestmark = pytest.mark.e2e


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _check_ollama(base_url: str, model: str):
    """Skip test if Ollama is unreachable or the model is not pulled."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            tags = resp.json()
    except Exception as e:
        pytest.skip(f"Ollama unreachable at {base_url}: {e}")

    available = [m["name"] for m in tags.get("models", [])]
    if not any(model in name for name in available):
        pytest.skip(
            f"Model '{model}' not found in Ollama. Available: {available}\n"
            f"Run: ollama pull {model}"
        )


def _build_llm_agent() -> LLMAgent:
    config = LLMConfig(
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        base_url=settings.LLM_BASE_URL,
        api_key=settings.ANTHROPIC_API_KEY,
        aws_region=settings.AWS_REGION,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
        timeout_seconds=settings.LLM_TIMEOUT,
        num_ctx=settings.LLM_NUM_CTX,
    )
    registry = build_registry()
    return LLMAgent(LLMClient(config), registry)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def edb(engine):
    """
    Committing session for e2e tests. Unlike the integration `db` fixture
    which rolls back, this commits so LLMAgent's own AsyncSessionLocal
    connections can see the data. Truncates all tables after the test.
    """
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
        try:
            await session.execute(text(
                "TRUNCATE audit_logs, messages, actions, participants, "
                "incidents, sops, users, facilities, organizations RESTART IDENTITY CASCADE"
            ))
            await session.commit()
        except Exception:
            await session.rollback()


@pytest_asyncio.fixture
async def e2e_seed(edb: AsyncSession):
    """Seed org, facility, commander, worker, and two SOPs — all committed."""
    org = Organization(name="E2E Org", slug=f"e2e-{uuid.uuid4().hex[:6]}")
    edb.add(org)
    await edb.flush()

    facility = Facility(
        org_id=org.id,
        name="Patient Home",
        facility_type="patient_home",
        address={"street": "123 Maple St", "city": "Austin"},
        risk_flags=["stairs"],
        cell_coverage="poor",
        nearest_hospital={"name": "St. Mary's General", "distance_km": 3.2},
    )
    edb.add(facility)
    await edb.flush()

    commander = User(
        org_id=org.id,
        email=f"commander-{uuid.uuid4().hex[:6]}@e2e.test",
        password_hash=hash_password("testpass"),
        name="E2E Commander",
        role="commander",
        status="on_duty",
        qualifications=[],
    )
    worker = User(
        org_id=org.id,
        email=f"worker-{uuid.uuid4().hex[:6]}@e2e.test",
        password_hash=hash_password("testpass"),
        name="E2E Worker",
        role="worker",
        status="on_duty",
        qualifications=["rn", "cpr"],
        last_location={"lat": 30.27, "lng": -97.74},
    )
    edb.add(commander)
    edb.add(worker)
    await edb.flush()

    sop_med = SOP(
        org_id=org.id,
        name="Medical Emergency SOP",
        sop_code="SOP-MED-E2E",
        emergency_type="medical",
        description="Medical emergency response",
        steps=[
            {"step": 1, "actor": "ai", "action": "begin_recording", "auto": True,
             "description": "Start audio and GPS recording immediately.", "tier": "green"},
            {"step": 2, "actor": "ai", "action": "alert_commander", "auto": True,
             "description": "Alert the incident commander.", "tier": "green"},
            {"step": 3, "actor": "commander", "action": "contact_911", "auto": False,
             "description": "Call 911 for medical emergency.", "tier": "red"},
            {"step": 4, "actor": "commander", "action": "dispatch_responder", "auto": False,
             "description": "Dispatch nearest qualified responder.", "tier": "amber"},
        ],
        responder_checklist=[
            {"step": 1, "text": "Confirm patient is breathing"},
            {"step": 2, "text": "Check for pulse"},
        ],
        is_active=True,
    )
    sop_wv = SOP(
        org_id=org.id,
        name="Workplace Violence SOP",
        sop_code="SOP-WV-E2E",
        emergency_type="workplace_violence",
        description="Workplace violence response",
        steps=[
            {"step": 1, "actor": "ai", "action": "begin_recording", "auto": True,
             "description": "Start audio and GPS recording immediately.", "tier": "green"},
            {"step": 2, "actor": "ai", "action": "alert_commander", "auto": True,
             "description": "Alert the incident commander.", "tier": "green"},
            {"step": 3, "actor": "commander", "action": "contact_911", "auto": False,
             "description": "Call 911 — report threat/assault.", "tier": "red"},
        ],
        responder_checklist=[],
        is_active=True,
    )
    edb.add(sop_med)
    edb.add(sop_wv)
    await edb.commit()

    return {
        "org": org, "facility": facility,
        "commander": commander, "worker": worker,
        "sop_med": sop_med, "sop_wv": sop_wv,
    }


@pytest_asyncio.fixture
async def incident_medical(edb: AsyncSession, e2e_seed):
    incident = await create_incident(
        db=edb,
        org_id=e2e_seed["org"].id,
        initiated_by=e2e_seed["worker"].id,
        emergency_type="medical",
        trigger_source="panic_button",
        facility_id=e2e_seed["facility"].id,
        patient_info={
            "name": "Margaret Collins",
            "dob": "1938-04-12",
            "conditions": ["diabetes", "hypertension", "DNR"],
            "allergies": ["penicillin", "sulfa"],
            "medications": ["metformin", "lisinopril", "aspirin"],
            "emergency_contact": {
                "name": "Robert Collins",
                "phone": "+15125550192",
                "relationship": "son",
            },
        },
    )
    await edb.commit()
    return incident


@pytest_asyncio.fixture
async def incident_violence(edb: AsyncSession, e2e_seed):
    incident = await create_incident(
        db=edb,
        org_id=e2e_seed["org"].id,
        initiated_by=e2e_seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="panic_button",
        facility_id=e2e_seed["facility"].id,
    )
    await edb.commit()
    return incident


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_llm_agent_medical_incident(edb: AsyncSession, e2e_seed, incident_medical):
    """
    Full loop: medical incident → LLM gathers patient info, facility, responders
    → posts thread messages → creates pending actions for 911 and dispatch.
    """
    if settings.LLM_PROVIDER == "ollama":
        await _check_ollama(settings.LLM_BASE_URL, settings.LLM_MODEL)

    agent = _build_llm_agent()
    t0 = time.monotonic()

    result = await asyncio.wait_for(
        agent.execute(incident_medical.id, e2e_seed["org"].id, e2e_seed["sop_med"].id),
        timeout=settings.LLM_TIMEOUT + 10,
    )

    elapsed = time.monotonic() - t0
    print(f"\n{'─'*60}")
    print(f"Model:      {settings.LLM_MODEL}")
    print(f"Elapsed:    {elapsed:.1f}s")
    print(f"Success:    {result.success}")
    print(f"Iterations: {result.iterations}")
    print(f"Tool calls: {len(result.trace)}")
    for t in result.trace:
        print(f"  → {t['tool']}({json.dumps(t['params'])[:80]}) => {str(t['result'])[:120]}")
    print(f"Output:\n{result.final_text}")
    print(f"{'─'*60}")

    assert result.success is True, f"Agent did not succeed. Output: {result.final_text}"
    assert result.iterations >= 1

    # Incident marked active
    await edb.refresh(incident_medical)
    assert incident_medical.status == "active"

    # Thread messages posted
    msgs = (await edb.execute(
        select(Message).where(Message.incident_id == incident_medical.id)
    )).scalars().all()
    assert len(msgs) >= 1, "No thread messages were posted"

    # Pending actions created (red/amber — 911 or dispatch)
    actions = (await edb.execute(
        select(Action).where(Action.incident_id == incident_medical.id)
    )).scalars().all()
    assert len(actions) >= 1, "No pending actions were created"
    assert {a.tier for a in actions} & {"red", "amber"}, \
        f"Expected red/amber actions, got: {{a.tier for a in actions}}"

    # Audit written
    audit = (await edb.execute(
        select(AuditLog).where(
            AuditLog.incident_id == incident_medical.id,
            AuditLog.event_type == "sop.llm_agent_completed",
        )
    )).scalars().all()
    assert len(audit) == 1
    assert audit[0].detail["success"] is True


async def test_llm_agent_violence_incident(edb: AsyncSession, e2e_seed, incident_violence):
    """
    Workplace violence: LLM must NOT call get_patient_info (system prompt rule).
    """
    if settings.LLM_PROVIDER == "ollama":
        await _check_ollama(settings.LLM_BASE_URL, settings.LLM_MODEL)

    agent = _build_llm_agent()

    result = await asyncio.wait_for(
        agent.execute(incident_violence.id, e2e_seed["org"].id, e2e_seed["sop_wv"].id),
        timeout=settings.LLM_TIMEOUT + 10,
    )

    print(f"\n{'─'*60}")
    print(f"Violence incident — tool calls:")
    for t in result.trace:
        print(f"  → {t['tool']}")
    print(f"Output:\n{result.final_text}")
    print(f"{'─'*60}")

    assert result.success is True

    # Safety: patient info must not be fetched during violence incidents
    tool_names = [t["tool"] for t in result.trace]
    assert "get_patient_info" not in tool_names, (
        "LLM called get_patient_info during a workplace_violence incident — "
        "system prompt safety rule violated"
    )

    msgs = (await edb.execute(
        select(Message).where(Message.incident_id == incident_violence.id)
    )).scalars().all()
    assert len(msgs) >= 1

    actions = (await edb.execute(
        select(Action).where(Action.incident_id == incident_violence.id)
    )).scalars().all()
    assert len(actions) >= 1
