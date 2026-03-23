"""Unit tests for data_tools.py — all DB calls are mocked."""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.tools.data_tools import (
    _haversine,
    get_incident_details,
    get_worker_profile,
    get_facility_info,
    get_patient_info,
    get_available_responders,
    get_sop,
    get_incident_history,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_db(scalar_value=None, scalar_list=None):
    """Build a mock AsyncSession with a pre-configured execute return value."""
    db = AsyncMock()
    result = MagicMock()

    if scalar_list is not None:
        scalars = MagicMock()
        scalars.all.return_value = scalar_list
        result.scalars.return_value = scalars
    else:
        result.scalar_one_or_none.return_value = scalar_value

    db.execute.return_value = result
    return db


def _fake_incident(**kwargs):
    inc = MagicMock()
    inc.id = uuid.uuid4()
    inc.incident_number = "INC-2026-0001"
    inc.status = "active"
    inc.emergency_type = kwargs.get("emergency_type", "medical")
    inc.trigger_source = "ui_button"
    inc.severity = 3
    inc.facility_id = uuid.uuid4()
    inc.commander_id = uuid.uuid4()
    inc.initiated_by = uuid.uuid4()
    inc.location = {"lat": 30.27, "lng": -97.74}
    inc.patient_info = {"name": "Mr. Smith", "allergies": ["penicillin"]}
    inc.initiated_at = MagicMock()
    inc.initiated_at.isoformat.return_value = "2026-03-16T10:00:00"
    for k, v in kwargs.items():
        setattr(inc, k, v)
    return inc


def _fake_user(**kwargs):
    u = MagicMock()
    u.id = uuid.uuid4()
    u.name = "Test User"
    u.role = kwargs.get("role", "worker")
    u.phone = "555-1234"
    u.status = kwargs.get("status", "on_duty")
    u.qualifications = kwargs.get("qualifications", [])
    u.medical_flags = []
    u.last_location = kwargs.get("last_location", {})
    for k, v in kwargs.items():
        setattr(u, k, v)
    return u


def _fake_facility():
    f = MagicMock()
    f.id = uuid.uuid4()
    f.name = "Test Facility"
    f.facility_type = "patient_home"
    f.address = {"street": "123 Main St"}
    f.risk_flags = []
    f.cell_coverage = "good"
    f.nearest_hospital = {"name": "City Hospital"}
    f.notes = None
    return f


def _fake_sop():
    s = MagicMock()
    s.id = uuid.uuid4()
    s.name = "Medical SOP"
    s.sop_code = "SOP-MED-001"
    s.emergency_type = "medical"
    s.description = "Medical emergency protocol"
    s.steps = [{"step": 1, "action": "begin_recording"}]
    s.responder_checklist = []
    return s


# ── _haversine ────────────────────────────────────────────────────────────────

def test_haversine_same_point():
    assert _haversine(30.0, -97.0, 30.0, -97.0) == pytest.approx(0.0, abs=0.001)


def test_haversine_known_distance():
    # Austin, TX to Houston, TX is ~235 km by straight line
    dist = _haversine(30.2672, -97.7431, 29.7604, -95.3698)
    assert 220 < dist < 260


def test_haversine_symmetric():
    d1 = _haversine(30.0, -97.0, 31.0, -98.0)
    d2 = _haversine(31.0, -98.0, 30.0, -97.0)
    assert d1 == pytest.approx(d2, rel=1e-6)


# ── get_incident_details ──────────────────────────────────────────────────────

async def test_get_incident_details_found():
    inc = _fake_incident()
    db = _mock_db(scalar_value=inc)
    result = await get_incident_details(db, str(inc.id))
    assert result["id"] == str(inc.id)
    assert result["status"] == "active"
    assert result["emergency_type"] == "medical"
    assert "patient_info" in result


async def test_get_incident_details_not_found():
    db = _mock_db(scalar_value=None)
    result = await get_incident_details(db, str(uuid.uuid4()))
    assert "error" in result


# ── get_worker_profile ────────────────────────────────────────────────────────

async def test_get_worker_profile_found():
    user = _fake_user(qualifications=["rn", "cpr"])
    db = _mock_db(scalar_value=user)
    result = await get_worker_profile(db, str(user.id))
    assert result["name"] == "Test User"
    assert "rn" in result["qualifications"]


async def test_get_worker_profile_not_found():
    db = _mock_db(scalar_value=None)
    result = await get_worker_profile(db, str(uuid.uuid4()))
    assert "error" in result


# ── get_facility_info ─────────────────────────────────────────────────────────

async def test_get_facility_info_found():
    fac = _fake_facility()
    db = _mock_db(scalar_value=fac)
    result = await get_facility_info(db, str(fac.id))
    assert result["name"] == "Test Facility"
    assert result["cell_coverage"] == "good"


async def test_get_facility_info_not_found():
    db = _mock_db(scalar_value=None)
    result = await get_facility_info(db, str(uuid.uuid4()))
    assert "error" in result


# ── get_patient_info ──────────────────────────────────────────────────────────

async def test_get_patient_info_active_incident():
    inc = _fake_incident()
    db = _mock_db(scalar_value=inc)
    result = await get_patient_info(db, str(uuid.uuid4()))
    assert result["patient_info"] == inc.patient_info


async def test_get_patient_info_no_active_incident():
    db = _mock_db(scalar_value=None)
    result = await get_patient_info(db, str(uuid.uuid4()))
    assert result["patient_info"] is None


# ── get_available_responders ──────────────────────────────────────────────────

async def test_get_available_responders_ranked_by_distance():
    # Two users at different distances from (30.27, -97.74)
    near = _fake_user(last_location={"lat": 30.28, "lng": -97.75})  # ~1 km
    far = _fake_user(last_location={"lat": 30.50, "lng": -97.90})   # ~30 km

    db = _mock_db(scalar_list=[far, near])  # deliberately wrong order
    result = await get_available_responders(db, str(uuid.uuid4()), 30.27, -97.74)
    responders = result["responders"]
    assert len(responders) == 2
    assert responders[0]["distance_km"] < responders[1]["distance_km"]


async def test_get_available_responders_qualification_filter():
    rn_user = _fake_user(qualifications=["rn", "cpr"], last_location={"lat": 30.27, "lng": -97.74})
    no_rn = _fake_user(qualifications=["cpr"], last_location={"lat": 30.27, "lng": -97.74})

    db = _mock_db(scalar_list=[rn_user, no_rn])
    result = await get_available_responders(
        db, str(uuid.uuid4()), 30.27, -97.74, qualification_filter=["rn"]
    )
    assert len(result["responders"]) == 1
    assert "rn" in result["responders"][0]["qualifications"]


async def test_get_available_responders_no_location_gets_high_distance():
    user = _fake_user(last_location={})  # no location data
    db = _mock_db(scalar_list=[user])
    result = await get_available_responders(db, str(uuid.uuid4()), 30.27, -97.74)
    assert result["responders"][0]["distance_km"] == 999.0


async def test_get_available_responders_empty():
    db = _mock_db(scalar_list=[])
    result = await get_available_responders(db, str(uuid.uuid4()), 30.27, -97.74)
    assert result["responders"] == []


# ── get_sop ───────────────────────────────────────────────────────────────────

async def test_get_sop_found():
    sop = _fake_sop()
    db = _mock_db(scalar_value=sop)
    result = await get_sop(db, str(uuid.uuid4()), "medical")
    assert result["emergency_type"] == "medical"
    assert "steps" in result


async def test_get_sop_not_found():
    db = _mock_db(scalar_value=None)
    result = await get_sop(db, str(uuid.uuid4()), "unknown_type")
    assert "error" in result
    assert "unknown_type" in result["error"]


# ── get_incident_history ──────────────────────────────────────────────────────

async def test_get_incident_history_returns_list():
    inc1 = _fake_incident()
    inc1.initiated_at.isoformat.return_value = "2026-01-01T00:00:00"
    inc2 = _fake_incident()
    inc2.initiated_at.isoformat.return_value = "2026-02-01T00:00:00"

    db = _mock_db(scalar_list=[inc1, inc2])
    result = await get_incident_history(db, str(uuid.uuid4()))
    assert len(result["history"]) == 2
    for item in result["history"]:
        assert "emergency_type" in item
        assert "severity" in item


async def test_get_incident_history_empty():
    db = _mock_db(scalar_list=[])
    result = await get_incident_history(db, str(uuid.uuid4()))
    assert result["history"] == []
