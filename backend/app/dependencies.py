import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from jwt.exceptions import InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from app.config import settings
from app.database import get_db
from app.models import User

logger = logging.getLogger(__name__)
security = HTTPBearer()


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except InvalidTokenError:
        logger.warning("JWT decode failed: invalid or expired token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"Auth failed: user {user_id} from token not found in database")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def user_has_role(user: User, *roles: str) -> bool:
    """Return True if the user has any of the given roles."""
    return any(r in roles for r in (user.roles or []))


async def require_commander(current_user: User = Depends(get_current_user)) -> User:
    if not user_has_role(current_user, "commander", "admin", "org_admin", "super_admin"):
        logger.warning(f"Authorization denied: user {current_user.id} [roles={current_user.roles}] requires commander/admin")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Commander role required")
    return current_user


async def require_org_admin(current_user: User = Depends(get_current_user)) -> User:
    if not user_has_role(current_user, "org_admin", "super_admin"):
        logger.warning(f"Authorization denied: user {current_user.id} [roles={current_user.roles}] requires org_admin/super_admin")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Org admin access required")
    return current_user


async def require_super_admin(current_user: User = Depends(get_current_user)) -> User:
    if not user_has_role(current_user, "super_admin"):
        logger.warning(f"Authorization denied: user {current_user.id} [roles={current_user.roles}] requires super_admin")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    return current_user
