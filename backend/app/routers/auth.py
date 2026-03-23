import logging
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User

logger = logging.getLogger(__name__)
from app.schemas.auth import (
    LoginRequest, TokenResponse, RefreshRequest, RefreshResponse,
    DeviceRegistrationRequest, UpdateProfileRequest, UserOut,
)
from app.services.auth_service import (
    authenticate_user, create_access_token, create_refresh_token, decode_token,
)

router = APIRouter(prefix="/api/v1", tags=["auth"])

ALLOWED_UPDATE_FIELDS = {"status", "last_location", "phone"}
ALLOWED_STATUSES = {"active", "on_duty", "off_duty"}


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, body.email, body.password)
    if not user:
        logger.warning(f"Failed login attempt for email={body.email!r}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    logger.info(f"User {user.id} logged in [role={user.role}, org={user.org_id}]")
    access_token = create_access_token(str(user.id), role=user.role)
    refresh_token = create_refresh_token(str(user.id))

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user={"id": str(user.id), "name": user.name, "role": user.role, "org_id": str(user.org_id)},
    )


@router.post("/auth/refresh", response_model=RefreshResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        logger.warning("Token refresh failed: invalid or non-refresh token")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"Token refresh failed: user {user_id} not found")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    logger.info(f"Access token refreshed for user {user.id}")
    return RefreshResponse(access_token=create_access_token(str(user.id), role=user.role))


@router.put("/users/me/device")
async def register_device(
    body: DeviceRegistrationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.device_info = {
        "push_token": body.push_token,
        "platform": body.platform,
        "device_model": body.device_model,
    }
    await db.commit()
    logger.info(f"Device registered for user {current_user.id} [platform={body.platform}]")
    return {"status": "registered"}


@router.patch("/users/me", response_model=UserOut)
async def update_profile(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    update_data = body.model_dump(exclude_none=True)

    # Validate status value
    if "status" in update_data and update_data["status"] not in ALLOWED_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {ALLOWED_STATUSES}")

    if "status" in update_data:
        current_user.status = update_data["status"]
    if "last_location" in update_data:
        current_user.last_location = update_data["last_location"]
    if "phone" in update_data:
        current_user.phone = update_data["phone"]

    await db.commit()
    await db.refresh(current_user)
    return current_user
