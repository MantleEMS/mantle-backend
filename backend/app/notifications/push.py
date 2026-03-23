"""
Push notification service via Firebase Cloud Messaging (FCM).
Falls back to no-op if Firebase credentials are not configured.
"""

import logging
from uuid import UUID
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.config import settings
from app.models import User, Incident

logger = logging.getLogger(__name__)

_firebase_initialized = False


def _init_firebase():
    global _firebase_initialized
    if _firebase_initialized:
        return True
    if not settings.FIREBASE_CREDENTIALS_PATH:
        return False
    try:
        import firebase_admin
        from firebase_admin import credentials
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        logger.info("Firebase Admin SDK initialized")
        return True
    except Exception as e:
        logger.warning(f"Firebase initialization failed: {e}")
        return False


async def send_push_notification(
    db: AsyncSession,
    user_id: UUID,
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    """Send push notification to a specific user's registered device."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return

    device_info = user.device_info or {}
    push_token = device_info.get("push_token")
    if not push_token:
        logger.debug(f"No push token for user {user_id}")
        return

    platform = device_info.get("platform", "android")
    logger.info(f"[PUSH] To {user.name} ({platform}): {title} — {body}")

    if not _init_firebase():
        # Log the notification but don't fail
        logger.debug(f"[PUSH SKIPPED - no Firebase config] {title}: {body}")
        return

    try:
        from firebase_admin import messaging
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=push_token,
        )
        response = messaging.send(message)
        logger.info(f"Push sent successfully: {response}")
    except Exception as e:
        logger.error(f"Failed to send push notification: {e}")


async def send_push_to_commanders(
    db: AsyncSession,
    org_id: UUID,
    incident: Incident,
    initiator_name: str,
):
    """Notify all active commanders about a new SOS incident."""
    result = await db.execute(
        select(User).where(
            and_(
                User.org_id == org_id,
                User.role == "commander",
                User.status != "inactive",
            )
        )
    )
    commanders = result.scalars().all()

    for commander in commanders:
        await send_push_notification(
            db=db,
            user_id=commander.id,
            title=f"SOS: {incident.emergency_type.replace('_', ' ').title()}",
            body=f"{initiator_name} triggered emergency. Incident {incident.incident_number}.",
            data={"deep_link": f"mantle://incident/{incident.id}/commander"},
        )


async def send_dispatch_push(
    db: AsyncSession,
    responder_id: UUID,
    incident: Incident,
    initiator_name: str,
    address: str = "",
    eta_minutes: int = None,
):
    """Notify a responder that they've been dispatched."""
    eta_str = f" • ETA {eta_minutes}m" if eta_minutes else ""
    await send_push_notification(
        db=db,
        user_id=responder_id,
        title=f"DISPATCH: {incident.emergency_type.replace('_', ' ').title()}",
        body=f"{initiator_name} • {address}{eta_str}",
        data={"deep_link": f"mantle://incident/{incident.id}/responder"},
    )
