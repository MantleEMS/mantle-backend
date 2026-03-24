"""
Telemetry retention: downsampling and purging for monitoring_sessions / telemetry_events.

Retention policy:
  - Hot  (0–7 days):   full resolution, all event types
  - Warm (7–90 days):  location events downsampled to 1 per minute per session
  - Cold (90+ days):   all telemetry deleted for ended/escalated sessions
                       (incident audit trail is preserved in incident_event_log)

These functions are safe to call repeatedly. Run them on a schedule (e.g. nightly
via pg_cron, Celery beat, or an admin endpoint).
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

# Configurable thresholds
DOWNSAMPLE_AFTER_DAYS = 7
PURGE_AFTER_DAYS = 90
DOWNSAMPLE_BATCH_SIZE = 500


async def downsample_location_events(db: AsyncSession) -> int:
    """
    For location events older than DOWNSAMPLE_AFTER_DAYS, keep only the first
    event per (session_id, minute) and delete the rest.

    Returns the number of rows deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=DOWNSAMPLE_AFTER_DAYS)

    # Use a CTE to identify rows to keep: the earliest event in each minute bucket.
    # Delete all other location events in the same bucket.
    result = await db.execute(
        text("""
            WITH ranked AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY session_id, date_trunc('minute', recorded_at)
                        ORDER BY recorded_at ASC
                    ) AS rn
                FROM telemetry_events
                WHERE event_type = 'location'
                  AND recorded_at < :cutoff
            )
            DELETE FROM telemetry_events
            WHERE id IN (
                SELECT id FROM ranked WHERE rn > 1
            )
        """),
        {"cutoff": cutoff},
    )
    await db.commit()
    return result.rowcount


async def purge_old_telemetry(db: AsyncSession) -> int:
    """
    Delete all telemetry events older than PURGE_AFTER_DAYS days, but only
    for sessions that have ended or escalated (not active sessions).

    Incident audit data is safe — it lives in incident_event_log, not here.

    Returns the number of rows deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=PURGE_AFTER_DAYS)

    result = await db.execute(
        text("""
            DELETE FROM telemetry_events
            WHERE recorded_at < :cutoff
              AND session_id IN (
                  SELECT id FROM monitoring_sessions
                  WHERE status IN ('ended', 'escalated')
              )
        """),
        {"cutoff": cutoff},
    )
    await db.commit()
    return result.rowcount


async def run_retention(db: AsyncSession) -> dict:
    """Run both retention passes. Returns counts for logging/reporting."""
    downsampled = await downsample_location_events(db)
    purged = await purge_old_telemetry(db)
    return {"downsampled": downsampled, "purged": purged}
