"""Unit tests for action_tools.py — DB and service calls are mocked."""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tools.action_tools import (
    start_evidence_collection,
    alert_commander,
    create_pending_action,
    post_thread_message,
    update_incident,
)


def _fake_incident(emergency_type="workplace_violence"):
    inc = MagicMock()
    inc.id = uuid.uuid4()
    inc.incident_number = "INC-2026-0001"
    inc.emergency_type = emergency_type
    inc.commander_id = uuid.uuid4()
    inc.org_id = uuid.uuid4()
    return inc


def _mock_db(scalar_value=None):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_value
    db.execute.return_value = result
    return db


# ── start_evidence_collection ─────────────────────────────────────────────────

async def test_start_evidence_collection_creates_message():
    incident_id = str(uuid.uuid4())
    db = AsyncMock()

    with patch("app.tools.action_tools.create_message", new_callable=AsyncMock) as mock_msg, \
         patch("app.tools.action_tools.write_audit", new_callable=AsyncMock):
        mock_msg.return_value = MagicMock()
        result = await start_evidence_collection(db, incident_id, ["audio", "gps"])

    mock_msg.assert_awaited_once()
    assert result["status"] == "started"
    assert "audio" in result["media_types"]


async def test_start_evidence_collection_calls_audit():
    incident_id = str(uuid.uuid4())
    db = AsyncMock()

    with patch("app.tools.action_tools.create_message", new_callable=AsyncMock) as mock_msg, \
         patch("app.tools.action_tools.write_audit", new_callable=AsyncMock) as mock_audit:
        mock_msg.return_value = MagicMock()
        await start_evidence_collection(db, incident_id, ["audio"])

    mock_audit.assert_awaited_once()
    call_kwargs = mock_audit.call_args.kwargs
    assert call_kwargs["event_type"] == "evidence.collection_started"


# ── alert_commander ───────────────────────────────────────────────────────────

async def test_alert_commander_creates_message():
    incident = _fake_incident()
    db = _mock_db(scalar_value=incident)

    with patch("app.tools.action_tools.create_message", new_callable=AsyncMock) as mock_msg, \
         patch("app.notifications.push.send_push_notification", new_callable=AsyncMock):
        mock_msg.return_value = MagicMock()
        result = await alert_commander(db, str(incident.id), str(incident.commander_id), "high")

    mock_msg.assert_awaited_once()
    assert result["status"] == "alerted"
    assert result["priority"] == "high"


async def test_alert_commander_sends_push_when_incident_has_commander():
    incident = _fake_incident()
    db = _mock_db(scalar_value=incident)

    with patch("app.tools.action_tools.create_message", new_callable=AsyncMock) as mock_msg, \
         patch("app.notifications.push.send_push_notification", new_callable=AsyncMock) as mock_push:
        mock_msg.return_value = MagicMock()
        await alert_commander(db, str(incident.id), str(incident.commander_id), "critical")

    mock_push.assert_awaited_once()


async def test_alert_commander_no_push_when_incident_not_found():
    db = _mock_db(scalar_value=None)

    with patch("app.tools.action_tools.create_message", new_callable=AsyncMock) as mock_msg, \
         patch("app.notifications.push.send_push_notification", new_callable=AsyncMock) as mock_push:
        mock_msg.return_value = MagicMock()
        await alert_commander(db, str(uuid.uuid4()), str(uuid.uuid4()), "high")

    mock_push.assert_not_awaited()


# ── create_pending_action ─────────────────────────────────────────────────────

async def test_create_pending_action_calls_create_action():
    incident = _fake_incident()
    db = _mock_db(scalar_value=incident)

    fake_action = MagicMock()
    fake_action.id = uuid.uuid4()

    with patch("app.tools.action_tools.create_action", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = fake_action
        result = await create_pending_action(
            db, str(incident.id), "red", "dispatch_responder",
            "Dispatch nearest responder"
        )

    mock_create.assert_awaited_once()
    assert result["status"] == "pending"
    assert result["tier"] == "red"
    assert result["action_type"] == "dispatch_responder"


async def test_create_pending_action_with_assigned_to():
    incident = _fake_incident()
    assigned_id = str(uuid.uuid4())
    db = _mock_db(scalar_value=incident)

    fake_action = MagicMock()
    fake_action.id = uuid.uuid4()

    with patch("app.tools.action_tools.create_action", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = fake_action
        await create_pending_action(
            db, str(incident.id), "amber", "dispatch_responder",
            "Dispatch responder", assigned_to=assigned_id
        )

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["assigned_to"] is not None


# ── post_thread_message ───────────────────────────────────────────────────────

async def test_post_thread_message_calls_create_message():
    incident_id = str(uuid.uuid4())
    db = AsyncMock()

    fake_msg = MagicMock()
    fake_msg.id = uuid.uuid4()
    fake_msg.seq = 5

    with patch("app.tools.action_tools.create_message", new_callable=AsyncMock) as mock_msg:
        mock_msg.return_value = fake_msg
        result = await post_thread_message(db, incident_id, "SOP started.", "system_event")

    mock_msg.assert_awaited_once()
    call_kwargs = mock_msg.call_args.kwargs
    assert call_kwargs["content"] == "SOP started."
    assert call_kwargs["message_type"] == "system_event"
    assert call_kwargs["sender_type"] == "ai"
    assert result["seq"] == 5


# ── update_incident ───────────────────────────────────────────────────────────

async def test_update_incident_allowed_fields():
    incident = _fake_incident()
    incident.severity = 3
    db = _mock_db(scalar_value=incident)

    with patch("app.tools.action_tools.create_message", new_callable=AsyncMock) as mock_msg:
        mock_msg.return_value = MagicMock()
        result = await update_incident(db, str(incident.id), {"severity": 5})

    assert result["status"] == "updated"
    assert result["fields"]["severity"] == 5
    assert incident.severity == 5


async def test_update_incident_ignores_disallowed_fields():
    incident = _fake_incident()
    db = _mock_db(scalar_value=incident)

    with patch("app.tools.action_tools.create_message", new_callable=AsyncMock) as mock_msg:
        mock_msg.return_value = MagicMock()
        result = await update_incident(
            db, str(incident.id), {"severity": 4, "id": "malicious", "status": "resolved"}
        )

    # "id" and "status" are not in allowed fields
    assert "id" not in result["fields"]
    assert "status" not in result["fields"]
    assert result["fields"]["severity"] == 4


async def test_update_incident_not_found():
    db = _mock_db(scalar_value=None)
    result = await update_incident(db, str(uuid.uuid4()), {"severity": 5})
    assert "error" in result
