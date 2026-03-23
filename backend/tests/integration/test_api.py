"""
Integration tests for FastAPI endpoints.
Uses httpx.AsyncClient with the real app and a test DB session.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.database import get_db


pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def client(db: AsyncSession):
    """Test HTTP client with the DB dependency overridden to the test session."""
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _get_token(client: AsyncClient, email: str, password: str = "testpass") -> str:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


# ── Health check ──────────────────────────────────────────────────────────────

async def test_health(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── Auth ──────────────────────────────────────────────────────────────────────

async def test_login_success(client: AsyncClient, seed):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": seed["worker"].email, "password": "testpass"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


async def test_login_wrong_password(client: AsyncClient, seed):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": seed["worker"].email, "password": "wrong"},
    )
    assert resp.status_code == 401


async def test_login_unknown_email(client: AsyncClient):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@test.com", "password": "pass"},
    )
    assert resp.status_code == 401


async def test_protected_endpoint_requires_auth(client: AsyncClient):
    resp = await client.get("/api/v1/incidents")
    # FastAPI HTTPBearer returns 403 when Authorization header is absent
    assert resp.status_code in (401, 403)


# ── Incidents ─────────────────────────────────────────────────────────────────

async def test_trigger_incident(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    resp = await client.post(
        "/api/v1/incidents",
        json={
            "emergency_type": "workplace_violence",
            "trigger_source": "ui_button",
            "facility_id": str(seed["facility"].id),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["emergency_type"] == "workplace_violence"
    assert body["status"] == "triggered"
    assert "id" in body


async def test_trigger_incident_unknown_type(client: AsyncClient, seed):
    """Any emergency_type is valid (model doesn't restrict it)."""
    token = await _get_token(client, seed["worker"].email)
    resp = await client.post(
        "/api/v1/incidents",
        json={"emergency_type": "generic", "trigger_source": "voice"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201


async def test_get_incident(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)

    # Create one first
    create_resp = await client.post(
        "/api/v1/incidents",
        json={"emergency_type": "medical", "trigger_source": "voice"},
        headers={"Authorization": f"Bearer {token}"},
    )
    incident_id = create_resp.json()["id"]

    # Fetch it
    get_resp = await client.get(
        f"/api/v1/incidents/{incident_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert "incident" in body
    assert "participants" in body
    assert "messages" in body
    assert "pending_actions" in body


async def test_get_incident_not_found(client: AsyncClient, seed):
    import uuid
    token = await _get_token(client, seed["worker"].email)
    resp = await client.get(
        f"/api/v1/incidents/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_list_incidents(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    await client.post(
        "/api/v1/incidents",
        json={"emergency_type": "workplace_violence", "trigger_source": "ui_button"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/api/v1/incidents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 1


async def test_resolve_incident(client: AsyncClient, seed):
    worker_token = await _get_token(client, seed["worker"].email)
    commander_token = await _get_token(client, seed["commander"].email)

    create_resp = await client.post(
        "/api/v1/incidents",
        json={"emergency_type": "workplace_violence", "trigger_source": "ui_button"},
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    incident_id = create_resp.json()["id"]

    resolve_resp = await client.post(
        f"/api/v1/incidents/{incident_id}/resolve",
        json={"resolution_note": "Situation resolved."},
        headers={"Authorization": f"Bearer {commander_token}"},
    )
    assert resolve_resp.status_code == 200
    assert resolve_resp.json()["status"] == "resolved"


async def test_resolve_already_resolved_incident(client: AsyncClient, seed):
    worker_token = await _get_token(client, seed["worker"].email)
    commander_token = await _get_token(client, seed["commander"].email)

    create_resp = await client.post(
        "/api/v1/incidents",
        json={"emergency_type": "workplace_violence", "trigger_source": "ui_button"},
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    incident_id = create_resp.json()["id"]

    await client.post(
        f"/api/v1/incidents/{incident_id}/resolve",
        json={},
        headers={"Authorization": f"Bearer {commander_token}"},
    )
    # Second resolve should fail
    resp = await client.post(
        f"/api/v1/incidents/{incident_id}/resolve",
        json={},
        headers={"Authorization": f"Bearer {commander_token}"},
    )
    assert resp.status_code == 400


# ── Actions ───────────────────────────────────────────────────────────────────

async def test_approve_action(client: AsyncClient, seed, db: AsyncSession):
    from app.services.incident_service import create_incident
    from app.services.action_service import create_action

    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    action = await create_action(
        db=db, incident_id=incident.id, org_id=seed["org"].id,
        action_type="dispatch_responder", description="Dispatch",
        tier="amber",
    )

    commander_token = await _get_token(client, seed["commander"].email)
    resp = await client.post(
        f"/api/v1/incidents/{incident.id}/actions/{action.id}/approve",
        json={},
        headers={"Authorization": f"Bearer {commander_token}"},
    )
    assert resp.status_code == 200


async def test_reject_action(client: AsyncClient, seed, db: AsyncSession):
    from app.services.incident_service import create_incident
    from app.services.action_service import create_action

    incident = await create_incident(
        db=db, org_id=seed["org"].id,
        initiated_by=seed["worker"].id,
        emergency_type="workplace_violence",
        trigger_source="ui_button",
    )
    action = await create_action(
        db=db, incident_id=incident.id, org_id=seed["org"].id,
        action_type="contact_911", description="Call 911",
        tier="red",
    )

    commander_token = await _get_token(client, seed["commander"].email)
    resp = await client.post(
        f"/api/v1/incidents/{incident.id}/actions/{action.id}/reject",
        json={"reason": "Not needed"},
        headers={"Authorization": f"Bearer {commander_token}"},
    )
    assert resp.status_code == 200


# ── Monitoring sessions ────────────────────────────────────────────────────────

async def test_start_monitoring_session(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    resp = await client.post(
        "/api/v1/monitoring/sessions",
        json={"check_in_interval_seconds": 300, "metadata": {"task": "home visit"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "active"
    assert body["check_in_interval_seconds"] == 300
    assert "id" in body


async def test_start_monitoring_session_requires_auth(client: AsyncClient, seed):
    resp = await client.post("/api/v1/monitoring/sessions", json={})
    assert resp.status_code in (401, 403)


async def test_end_monitoring_session(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)

    start_resp = await client.post(
        "/api/v1/monitoring/sessions",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    session_id = start_resp.json()["id"]

    end_resp = await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/end",
        json={"reason": "manual"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert end_resp.status_code == 200
    assert end_resp.json()["status"] == "ended"
    assert end_resp.json()["end_reason"] == "manual"


async def test_end_session_already_ended(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)

    start_resp = await client.post(
        "/api/v1/monitoring/sessions", json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    session_id = start_resp.json()["id"]
    await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/end",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/end",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


async def test_end_session_not_found(client: AsyncClient, seed):
    import uuid
    token = await _get_token(client, seed["worker"].email)
    resp = await client.post(
        f"/api/v1/monitoring/sessions/{uuid.uuid4()}/end",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


async def test_worker_cannot_end_another_workers_session(client: AsyncClient, seed, db):
    from app.services.monitoring_service import start_session
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["commander"].id)

    worker_token = await _get_token(client, seed["worker"].email)
    resp = await client.post(
        f"/api/v1/monitoring/sessions/{session.id}/end",
        json={},
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert resp.status_code == 403


async def test_commander_can_end_workers_session(client: AsyncClient, seed, db):
    from app.services.monitoring_service import start_session
    session = await start_session(db=db, org_id=seed["org"].id, user_id=seed["worker"].id)

    commander_token = await _get_token(client, seed["commander"].email)
    resp = await client.post(
        f"/api/v1/monitoring/sessions/{session.id}/end",
        json={"reason": "manual"},
        headers={"Authorization": f"Bearer {commander_token}"},
    )
    assert resp.status_code == 200


async def test_list_sessions_worker_sees_own(client: AsyncClient, seed):
    worker_token = await _get_token(client, seed["worker"].email)
    commander_token = await _get_token(client, seed["commander"].email)

    # Worker starts a session; commander starts a separate session
    await client.post("/api/v1/monitoring/sessions", json={}, headers={"Authorization": f"Bearer {worker_token}"})
    await client.post("/api/v1/monitoring/sessions", json={}, headers={"Authorization": f"Bearer {commander_token}"})

    resp = await client.get("/api/v1/monitoring/sessions", headers={"Authorization": f"Bearer {worker_token}"})
    assert resp.status_code == 200
    sessions = resp.json()
    user_ids = {s["user_id"] for s in sessions}
    assert all(uid == str(seed["worker"].id) for uid in user_ids)


async def test_list_sessions_commander_sees_all(client: AsyncClient, seed):
    worker_token = await _get_token(client, seed["worker"].email)
    commander_token = await _get_token(client, seed["commander"].email)

    await client.post("/api/v1/monitoring/sessions", json={}, headers={"Authorization": f"Bearer {worker_token}"})
    await client.post("/api/v1/monitoring/sessions", json={}, headers={"Authorization": f"Bearer {commander_token}"})

    resp = await client.get("/api/v1/monitoring/sessions", headers={"Authorization": f"Bearer {commander_token}"})
    assert resp.status_code == 200
    user_ids = {s["user_id"] for s in resp.json()}
    assert len(user_ids) >= 2


async def test_get_monitoring_session(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    start_resp = await client.post(
        "/api/v1/monitoring/sessions", json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    session_id = start_resp.json()["id"]

    resp = await client.get(
        f"/api/v1/monitoring/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == session_id


# ── Monitoring telemetry ───────────────────────────────────────────────────────

async def _start_session(client: AsyncClient, token: str) -> str:
    resp = await client.post(
        "/api/v1/monitoring/sessions", json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_submit_telemetry(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    session_id = await _start_session(client, token)

    resp = await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/telemetry",
        json={
            "events": [
                {"event_type": "location", "data": {"lat": 37.77, "lng": -122.41, "accuracy_m": 5.0}, "recorded_at": "2026-03-18T10:00:00Z"},
                {"event_type": "heart_rate", "data": {"bpm": 88}, "recorded_at": "2026-03-18T10:00:01Z"},
                {"event_type": "speed", "data": {"kmh": 2.5}, "recorded_at": "2026-03-18T10:00:02Z"},
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 3
    assert body["escalated"] is False
    assert body["incident_id"] is None


async def test_submit_telemetry_fall_detected_escalates(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    session_id = await _start_session(client, token)

    resp = await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/telemetry",
        json={
            "events": [
                {
                    "event_type": "fall_detected",
                    "data": {"confidence": 0.97, "location": {"lat": 37.77, "lng": -122.41}},
                    "recorded_at": "2026-03-18T10:00:00Z",
                }
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["escalated"] is True
    assert body["incident_id"] is not None

    # Confirm session is now escalated
    session_resp = await client.get(
        f"/api/v1/monitoring/sessions/{session_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert session_resp.json()["status"] == "escalated"
    assert session_resp.json()["incident_id"] == body["incident_id"]


async def test_submit_telemetry_on_ended_session_returns_400(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    session_id = await _start_session(client, token)
    await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/end",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/telemetry",
        json={"events": [{"event_type": "location", "data": {"lat": 37.77, "lng": -122.41}, "recorded_at": "2026-03-18T10:00:00Z"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


async def test_submit_telemetry_empty_events_returns_400(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    session_id = await _start_session(client, token)
    resp = await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/telemetry",
        json={"events": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


async def test_get_telemetry(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    session_id = await _start_session(client, token)

    await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/telemetry",
        json={
            "events": [
                {"event_type": "location", "data": {"lat": 37.77, "lng": -122.41}, "recorded_at": "2026-03-18T10:00:00Z"},
                {"event_type": "heart_rate", "data": {"bpm": 72}, "recorded_at": "2026-03-18T10:00:01Z"},
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        f"/api/v1/monitoring/sessions/{session_id}/telemetry",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_get_telemetry_filter_by_event_type(client: AsyncClient, seed):
    token = await _get_token(client, seed["worker"].email)
    session_id = await _start_session(client, token)

    await client.post(
        f"/api/v1/monitoring/sessions/{session_id}/telemetry",
        json={
            "events": [
                {"event_type": "location", "data": {"lat": 37.77, "lng": -122.41}, "recorded_at": "2026-03-18T10:00:00Z"},
                {"event_type": "heart_rate", "data": {"bpm": 72}, "recorded_at": "2026-03-18T10:00:01Z"},
            ]
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    resp = await client.get(
        f"/api/v1/monitoring/sessions/{session_id}/telemetry?event_type=location",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "location"
