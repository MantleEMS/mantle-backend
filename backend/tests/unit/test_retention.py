"""Unit tests for retention_service — DB calls are mocked."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.retention_service import (
    downsample_location_events,
    purge_old_telemetry,
    run_retention,
    DOWNSAMPLE_AFTER_DAYS,
    PURGE_AFTER_DAYS,
)


def _mock_db(rowcount=0):
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = rowcount
    db.execute.return_value = result
    return db


# ── downsample_location_events ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_downsample_executes_and_commits():
    db = _mock_db(rowcount=12)
    deleted = await downsample_location_events(db)
    assert db.execute.called
    assert db.commit.called
    assert deleted == 12


@pytest.mark.asyncio
async def test_downsample_returns_zero_when_nothing_to_remove():
    db = _mock_db(rowcount=0)
    deleted = await downsample_location_events(db)
    assert deleted == 0


@pytest.mark.asyncio
async def test_downsample_passes_cutoff_param():
    db = _mock_db()
    await downsample_location_events(db)
    # Verify the execute call received a dict with a "cutoff" key
    call_args = db.execute.call_args
    params = call_args[0][1]  # second positional arg is the params dict
    assert "cutoff" in params


# ── purge_old_telemetry ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_purge_executes_and_commits():
    db = _mock_db(rowcount=300)
    purged = await purge_old_telemetry(db)
    assert db.execute.called
    assert db.commit.called
    assert purged == 300


@pytest.mark.asyncio
async def test_purge_returns_zero_when_nothing_to_remove():
    db = _mock_db(rowcount=0)
    purged = await purge_old_telemetry(db)
    assert purged == 0


@pytest.mark.asyncio
async def test_purge_passes_cutoff_param():
    db = _mock_db()
    await purge_old_telemetry(db)
    call_args = db.execute.call_args
    params = call_args[0][1]
    assert "cutoff" in params


# ── run_retention ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_retention_returns_both_counts():
    db = AsyncMock()

    results = [MagicMock(rowcount=7), MagicMock(rowcount=42)]
    db.execute.side_effect = results

    summary = await run_retention(db)
    assert summary == {"downsampled": 7, "purged": 42}


@pytest.mark.asyncio
async def test_run_retention_calls_both_passes():
    db = AsyncMock()
    result = MagicMock(rowcount=0)
    db.execute.return_value = result

    await run_retention(db)

    # downsample + purge each call execute once
    assert db.execute.call_count == 2
