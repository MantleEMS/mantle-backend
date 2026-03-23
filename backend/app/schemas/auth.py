from pydantic import BaseModel, ConfigDict, EmailStr
from typing import Optional
from uuid import UUID


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    refresh_token: str
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str


class DeviceRegistrationRequest(BaseModel):
    push_token: str
    platform: str  # ios | android
    device_model: Optional[str] = None


class UpdateProfileRequest(BaseModel):
    status: Optional[str] = None
    last_location: Optional[dict] = None
    phone: Optional[str] = None


class UserOut(BaseModel):
    id: UUID
    name: str
    role: str
    status: str
    org_id: UUID

    model_config = ConfigDict(from_attributes=True)
