"""
E2E test fixtures.
Reuses the integration DB engine/session/seed fixtures and adds Ollama connectivity check.

Run inside Docker (all hostnames resolve correctly):
    docker compose up -d postgres redis
    docker compose run --rm api pytest -m e2e -v -s --log-cli-level=INFO
"""

import logging
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from tests.integration.conftest import TEST_DB_URL

# Re-export all integration fixtures (engine, db, seed) unchanged
from tests.integration.conftest import engine, db, seed  # noqa: F401


def pytest_collection_modifyitems(items):
    for item in items:
        if "e2e" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)


@pytest.fixture(autouse=True, scope="session")
def patch_async_session_local():
    """
    Modules import AsyncSessionLocal by name at load time, so patching
    app.database alone doesn't help — each module holds its own reference.
    Patch every module that opens its own sessions during agent execution.
    """
    import app.database
    import app.agent.llm_agent
    import app.tools.registry

    test_engine = create_async_engine(TEST_DB_URL, echo=False)
    test_session = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    app.database.AsyncSessionLocal = test_session
    app.agent.llm_agent.AsyncSessionLocal = test_session
    app.tools.registry.AsyncSessionLocal = test_session


@pytest.fixture(autouse=True, scope="session")
def configure_logging():
    """
    Ensure agent/tool loggers are at INFO level so pytest's own capture system
    picks them up for --log-cli-level, log_file, and report.html.
    Do NOT add custom handlers or set propagate=False — that bypasses pytest capture.
    """
    for name in (
        "app.agent.llm_client",
        "app.agent.llm_agent",
        "app.agent.router",
        "app.tools.registry",
        "app.tools.adaptive_tools",
        "app.services.incident_service",
        "app.services.action_service",
    ):
        logging.getLogger(name).setLevel(logging.INFO)
