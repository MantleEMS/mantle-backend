"""
Integration test fixtures.
Uses the Docker-managed PostgreSQL instance (localhost:5432) by default.
Override with TEST_DATABASE_URL env var if needed.

Run:
  docker compose up -d postgres redis   # start dependencies
  pytest -m integration                 # run integration tests

If postgres is unreachable the entire integration suite is skipped.
"""

import os
import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

from app.database import Base
from app.models import Organization, Facility, User, SOP
from app.services.auth_service import hash_password


# Default to Docker postgres; override via env var
_DEFAULT_ADMIN_URL = "postgresql+asyncpg://mantle:mantle_secret@localhost:5432/mantle_ems"
_DEFAULT_TEST_DB = "mantle_ems_test"
_DEFAULT_TEST_URL = f"postgresql+asyncpg://mantle:mantle_secret@localhost:5432/{_DEFAULT_TEST_DB}"

ADMIN_URL = os.environ.get("TEST_ADMIN_DATABASE_URL", _DEFAULT_ADMIN_URL)
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", _DEFAULT_TEST_URL)
TEST_DB_NAME = TEST_DB_URL.rsplit("/", 1)[-1]


def pytest_collection_modifyitems(items):
    """Auto-mark all tests in this package as integration."""
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


async def _ensure_test_db():
    """Create the test database if it doesn't exist. Uses AUTOCOMMIT (required for CREATE DATABASE)."""
    engine = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT", echo=False)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DB_NAME},
            )
            if not result.fetchone():
                await conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    """
    Session-scoped engine.
    - Skips if postgres is unreachable.
    - Creates the test DB if it doesn't exist.
    - Creates all tables once; drops them on teardown.
    """
    # Try to reach postgres
    try:
        await _ensure_test_db()
    except Exception as e:
        pytest.skip(f"PostgreSQL unreachable — skipping integration tests. ({e})")

    eng = create_async_engine(TEST_DB_URL, echo=False)
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        await eng.dispose()
        pytest.skip(f"Could not create test schema: {e}")

    yield eng

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine):
    """Per-test async session. Rolls back after each test to keep the DB clean."""
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def seed(db: AsyncSession):
    """Minimal seed: org, facility, commander, worker, two SOPs."""
    org = Organization(name="Test Org", slug=f"test-{uuid.uuid4().hex[:6]}")
    db.add(org)
    await db.flush()

    facility = Facility(
        org_id=org.id,
        name="Test Facility",
        facility_type="patient_home",
        address={"lat": 30.2672, "lng": -97.7431},
        risk_flags=[],
        cell_coverage="good",
        nearest_hospital={"name": "Test Hospital"},
    )
    db.add(facility)
    await db.flush()

    commander = User(
        org_id=org.id,
        email=f"commander-{uuid.uuid4().hex[:6]}@test.com",
        password_hash=hash_password("testpass"),
        name="Test Commander",
        roles=["commander"],
        status="on_duty",
        qualifications=[],
    )
    worker = User(
        org_id=org.id,
        email=f"worker-{uuid.uuid4().hex[:6]}@test.com",
        password_hash=hash_password("testpass"),
        name="Test Worker",
        roles=["worker"],
        status="on_duty",
        qualifications=["rn", "cpr"],
        last_location={"lat": 30.27, "lng": -97.74},
    )
    db.add(commander)
    db.add(worker)
    await db.flush()

    sop_wv = SOP(
        org_id=org.id,
        name="Workplace Violence SOP",
        sop_code="SOP-WV-TEST",
        emergency_type="workplace_violence",
        description="Test SOP",
        steps=[
            {"step": 1, "actor": "ai", "action": "begin_recording", "auto": True,
             "description": "Start recording", "tier": "green"},
            {"step": 2, "actor": "commander", "action": "dispatch_responder", "auto": False,
             "description": "Dispatch", "tier": "amber"},
        ],
        responder_checklist=[],
        is_active=True,
    )
    sop_med = SOP(
        org_id=org.id,
        name="Medical SOP",
        sop_code="SOP-MED-TEST",
        emergency_type="medical",
        description="Test medical SOP",
        steps=[
            {"step": 1, "actor": "ai", "action": "begin_recording", "auto": True,
             "description": "Start recording", "tier": "green"},
            {"step": 2, "actor": "commander", "action": "contact_911", "auto": False,
             "description": "Call 911", "tier": "red"},
        ],
        responder_checklist=[],
        is_active=True,
    )
    db.add(sop_wv)
    db.add(sop_med)
    await db.commit()

    return {
        "org": org,
        "facility": facility,
        "commander": commander,
        "worker": worker,
        "sop_wv": sop_wv,
        "sop_med": sop_med,
    }
