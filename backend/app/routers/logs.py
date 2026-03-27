import os
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse

from app.config import settings
from app.dependencies import require_org_admin
from app.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

LOG_FILES = {
    "app": "app.log",
    "trace": "trace.log",
}


def _resolve_log_path(name: str) -> str:
    filename = LOG_FILES.get(name)
    if not filename:
        raise HTTPException(status_code=404, detail=f"Log '{name}' not found")
    path = os.path.join(settings.LOGS_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Log file '{filename}' does not exist")
    return path


@router.get("/download/{name}")
async def download_log(
    name: str,
    current_user: User = Depends(require_org_admin),
):
    """Download a log file (app or trace). Restricted to org_admin / super_admin."""
    path = _resolve_log_path(name)
    logger.info(f"Log download: {name}.log requested by user {current_user.id}")
    return FileResponse(path, media_type="text/plain", filename=LOG_FILES[name])


@router.get("/tail/{name}", response_class=PlainTextResponse)
async def tail_log(
    name: str,
    lines: int = Query(default=100, ge=1, le=10000),
    current_user: User = Depends(require_org_admin),
):
    """Return the last N lines of a log file. Restricted to org_admin / super_admin."""
    path = _resolve_log_path(name)
    logger.info(f"Log tail: {name}.log ({lines} lines) requested by user {current_user.id}")
    with open(path, "r", errors="replace") as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:])
