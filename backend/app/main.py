import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.config import settings
from app.database import AsyncSessionLocal
from app.redis_client import get_redis, close_redis, redis_zrangebyscore, redis_zrem

os.makedirs(settings.LOGS_DIR, exist_ok=True)

# --- Custom TRACE level (below DEBUG=10) ---
TRACE = 5
logging.addLevelName(TRACE, "TRACE")


def _trace(self: logging.Logger, message: str, *args, **kwargs):
    if self.isEnabledFor(TRACE):
        self._log(TRACE, message, args, **kwargs)  # type: ignore[arg-type]


logging.Logger.trace = _trace  # type: ignore[attr-defined]

_log_formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")

_file_handler = RotatingFileHandler(
    os.path.join(settings.LOGS_DIR, "app.log"),
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
)
_file_handler.setFormatter(_log_formatter)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler])
logger = logging.getLogger(__name__)

# --- Trace log file (only written when TRACE_ENABLED=true) ---
if settings.TRACE_ENABLED:
    _trace_handler = RotatingFileHandler(
        os.path.join(settings.LOGS_DIR, "trace.log"),
        maxBytes=50 * 1024 * 1024,  # 50 MB
        backupCount=3,
    )
    _trace_handler.setFormatter(_log_formatter)
    _trace_handler.setLevel(TRACE)

    # Lower the root logger so TRACE records are created
    logging.getLogger().setLevel(TRACE)

    # Attach the trace handler only to the namespaces that emit trace events
    for _trace_ns in ("app.routers.threads", "app.services.monitoring_service"):
        _ns_logger = logging.getLogger(_trace_ns)
        _ns_logger.addHandler(_trace_handler)
        _ns_logger.setLevel(TRACE)

    logger.info("TRACE logging enabled → %s/trace.log", settings.LOGS_DIR)

# Route uvicorn loggers through our handlers so errors appear in app.log
for _uvicorn_logger in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _uv = logging.getLogger(_uvicorn_logger)
    _uv.handlers = [_file_handler, _console_handler]
    _uv.propagate = False

_urgency_poller_task: asyncio.Task | None = None


async def action_urgency_poller():
    """
    Runs every 1 second. Checks pending actions in Redis sorted set.
    Actions pending > 5 minutes are escalated (tier upgraded) or expired.
    """
    ESCALATE_AFTER_SEC = 300   # 5 minutes
    EXPIRE_AFTER_SEC = 1800    # 30 minutes

    while True:
        try:
            await asyncio.sleep(1)
            now = datetime.now(timezone.utc).timestamp()

            from app.models import Action
            from sqlalchemy import select

            # Check actions pending for too long
            stale = await redis_zrangebyscore(
                "action_urgency", 0, now - ESCALATE_AFTER_SEC
            )

            if stale:
                async with AsyncSessionLocal() as db:
                    import uuid
                    for action_id_str in stale:
                        try:
                            action_uuid = uuid.UUID(action_id_str)
                            result = await db.execute(
                                select(Action).where(Action.id == action_uuid)
                            )
                            action = result.scalar_one_or_none()
                            if not action:
                                await redis_zrem("action_urgency", action_id_str)
                                continue

                            if action.status != "pending":
                                # Already handled — clean up
                                await redis_zrem("action_urgency", action_id_str)
                                continue

                            age = now - action.created_at.timestamp()
                            if age > EXPIRE_AFTER_SEC:
                                action.status = "expired"
                                await db.commit()
                                await redis_zrem("action_urgency", action_id_str)
                                logger.info(f"Action {action_id_str} expired after {age:.0f}s")
                                from app.metrics import actions_expired
                                actions_expired.inc()
                            elif age > ESCALATE_AFTER_SEC and action.tier == "green":
                                action.tier = "amber"
                                await db.commit()
                                logger.info(f"Action {action_id_str} escalated green→amber")
                                from app.metrics import actions_escalated
                                actions_escalated.inc()

                        except Exception as e:
                            logger.error(f"Urgency poller error for {action_id_str}: {e}")

        except asyncio.CancelledError:
            logger.info("Action urgency poller stopped")
            return
        except Exception as e:
            logger.error(f"Action urgency poller unexpected error: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _urgency_poller_task

    # Run seed data if configured
    if settings.RUN_SEED:
        try:
            from app.seed.seed_data import run_seed
            await run_seed()
        except Exception:
            logger.exception("Seed data failed")

    # Start action urgency poller
    _urgency_poller_task = asyncio.create_task(action_urgency_poller())
    logger.info("Action urgency poller started")

    logger.info("Mantle EMS API ready")
    yield

    # Shutdown
    if _urgency_poller_task:
        _urgency_poller_task.cancel()
        try:
            await _urgency_poller_task
        except asyncio.CancelledError:
            pass

    await close_redis()
    logger.info("Mantle EMS API shutdown complete")


app = FastAPI(
    title="Mantle EMS API",
    description="Real-time emergency management system for home healthcare workers",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Register routers ---
from app.routers import auth, config, incidents, threads, actions, evidence, search, monitoring, logs, compliance

app.include_router(auth.router)
app.include_router(config.router)
app.include_router(incidents.router)
app.include_router(threads.router)
app.include_router(actions.router)
app.include_router(evidence.router)
app.include_router(search.router)
app.include_router(monitoring.router)
app.include_router(logs.router)
app.include_router(compliance.router)


# --- Prometheus HTTP metrics + /metrics endpoint ---
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=True)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mantle-ems-api"}


@app.get("/")
async def root():
    return {
        "service": "Mantle EMS API",
        "version": "1.0.0",
        "docs": "/docs",
    }
