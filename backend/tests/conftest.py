"""
Top-level pytest fixtures shared across unit and integration tests.
Patches Redis and push notifications globally so no test accidentally
connects to live external services.
"""

import pytest
from itertools import count
from unittest.mock import AsyncMock


@pytest.fixture(autouse=True)
def mock_redis(monkeypatch):
    """Patch Redis helper functions where they are imported/used."""
    monkeypatch.setattr("app.services.thread_service.redis_publish", AsyncMock(return_value=None))
    _seq = count(1)
    monkeypatch.setattr("app.services.thread_service.redis_incr", AsyncMock(side_effect=lambda *_: next(_seq)))
    monkeypatch.setattr("app.services.action_service.redis_zadd", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.action_service.redis_zrem", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.action_service.redis_publish", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def mock_agent_router(monkeypatch):
    """Prevent background SOP/LLM execution during tests — avoids DNS errors
    when the app's DATABASE_URL points to the Docker hostname 'postgres'."""
    monkeypatch.setattr("app.routers.incidents.agent_router.handle_incident", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def mock_push(monkeypatch):
    """Prevent any Firebase push notification from being sent during tests."""
    monkeypatch.setattr(
        "app.notifications.push.send_push_notification", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "app.services.incident_service.send_push_to_commanders", AsyncMock(return_value=None)
    )
