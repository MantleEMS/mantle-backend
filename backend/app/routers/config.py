from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import uuid
from typing import Optional

from app.database import get_db
from app.dependencies import get_current_user, require_org_admin, require_super_admin
from app.models import Organization, Facility, SOP, User
from app.schemas.config import (
    OrganizationOut, OrganizationCreate, OrganizationUpdate,
    FacilityOut, FacilityCreate, FacilityUpdate,
    SOPOut, SOPCreate, SOPUpdate,
    UserOut, UserCreate, UserUpdate,
)
from app.services.auth_service import hash_password

router = APIRouter(prefix="/api/v1", tags=["config"])

ELEVATED_ROLES = {"org_admin", "super_admin"}


# --- Organizations ---

@router.get("/orgs", response_model=list[OrganizationOut])
async def list_organizations(
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        result = await db.execute(select(Organization))
    else:
        result = await db.execute(
            select(Organization).where(Organization.id == current_user.org_id)
        )
    return result.scalars().all()


@router.post("/orgs", response_model=OrganizationOut, status_code=201)
async def create_organization(
    body: OrganizationCreate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    org = Organization(**body.model_dump())
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org


@router.get("/orgs/{org_id}", response_model=OrganizationOut)
async def get_organization(
    org_id: uuid.UUID,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "super_admin" and org_id != current_user.org_id:
        raise HTTPException(status_code=403, detail="Access denied")
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.put("/orgs/{org_id}", response_model=OrganizationOut)
async def update_organization(
    org_id: uuid.UUID,
    body: OrganizationUpdate,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "super_admin" and org_id != current_user.org_id:
        raise HTTPException(status_code=403, detail="Access denied")
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(org, field, value)
    await db.commit()
    await db.refresh(org)
    return org


@router.delete("/orgs/{org_id}", status_code=204)
async def delete_organization(
    org_id: uuid.UUID,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.delete(org)
    await db.commit()


# --- Facilities ---

@router.get("/facilities", response_model=list[FacilityOut])
async def list_facilities(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        result = await db.execute(select(Facility))
    else:
        result = await db.execute(
            select(Facility).where(Facility.org_id == current_user.org_id)
        )
    return result.scalars().all()


@router.post("/facilities", response_model=FacilityOut, status_code=201)
async def create_facility(
    body: FacilityCreate,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    data = body.model_dump(exclude={"org_id"})
    if current_user.role == "super_admin" and body.org_id:
        target_org_id = body.org_id
    else:
        target_org_id = current_user.org_id
    facility = Facility(org_id=target_org_id, **data)
    db.add(facility)
    await db.commit()
    await db.refresh(facility)
    return facility


@router.get("/facilities/{facility_id}", response_model=FacilityOut)
async def get_facility(
    facility_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(Facility).where(Facility.id == facility_id)
    else:
        query = select(Facility).where(
            and_(Facility.id == facility_id, Facility.org_id == current_user.org_id)
        )
    result = await db.execute(query)
    facility = result.scalar_one_or_none()
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    return facility


@router.put("/facilities/{facility_id}", response_model=FacilityOut)
async def update_facility(
    facility_id: uuid.UUID,
    body: FacilityUpdate,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(Facility).where(Facility.id == facility_id)
    else:
        query = select(Facility).where(
            and_(Facility.id == facility_id, Facility.org_id == current_user.org_id)
        )
    result = await db.execute(query)
    facility = result.scalar_one_or_none()
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(facility, field, value)
    await db.commit()
    await db.refresh(facility)
    return facility


@router.delete("/facilities/{facility_id}", status_code=204)
async def delete_facility(
    facility_id: uuid.UUID,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(Facility).where(Facility.id == facility_id)
    else:
        query = select(Facility).where(
            and_(Facility.id == facility_id, Facility.org_id == current_user.org_id)
        )
    result = await db.execute(query)
    facility = result.scalar_one_or_none()
    if not facility:
        raise HTTPException(status_code=404, detail="Facility not found")
    await db.delete(facility)
    await db.commit()


# --- SOPs ---

@router.get("/sops", response_model=list[SOPOut])
async def list_sops(
    emergency_type: str = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(SOP).where(SOP.is_active == True)
    else:
        query = select(SOP).where(and_(SOP.org_id == current_user.org_id, SOP.is_active == True))
    if emergency_type:
        query = query.where(SOP.emergency_type == emergency_type)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/sops", response_model=SOPOut, status_code=201)
async def create_sop(
    body: SOPCreate,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    data = body.model_dump(exclude={"org_id"})
    data["steps"] = [s if isinstance(s, dict) else s.model_dump() for s in body.steps]
    data["responder_checklist"] = [c if isinstance(c, dict) else c.model_dump() for c in body.responder_checklist]
    if current_user.role == "super_admin" and body.org_id:
        target_org_id = body.org_id
    else:
        target_org_id = current_user.org_id
    sop = SOP(org_id=target_org_id, **data)
    db.add(sop)
    await db.commit()
    await db.refresh(sop)
    return sop


@router.get("/sops/{sop_id}", response_model=SOPOut)
async def get_sop(
    sop_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(SOP).where(SOP.id == sop_id)
    else:
        query = select(SOP).where(and_(SOP.id == sop_id, SOP.org_id == current_user.org_id))
    result = await db.execute(query)
    sop = result.scalar_one_or_none()
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")
    return sop


@router.put("/sops/{sop_id}", response_model=SOPOut)
async def update_sop(
    sop_id: uuid.UUID,
    body: SOPUpdate,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(SOP).where(SOP.id == sop_id)
    else:
        query = select(SOP).where(and_(SOP.id == sop_id, SOP.org_id == current_user.org_id))
    result = await db.execute(query)
    sop = result.scalar_one_or_none()
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")
    data = body.model_dump(exclude_none=True)
    if "steps" in data:
        data["steps"] = [s if isinstance(s, dict) else s.model_dump() for s in body.steps]
    if "responder_checklist" in data:
        data["responder_checklist"] = [c if isinstance(c, dict) else c.model_dump() for c in body.responder_checklist]
    for field, value in data.items():
        setattr(sop, field, value)
    await db.commit()
    await db.refresh(sop)
    return sop


@router.delete("/sops/{sop_id}", status_code=204)
async def delete_sop(
    sop_id: uuid.UUID,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(SOP).where(SOP.id == sop_id)
    else:
        query = select(SOP).where(and_(SOP.id == sop_id, SOP.org_id == current_user.org_id))
    result = await db.execute(query)
    sop = result.scalar_one_or_none()
    if not sop:
        raise HTTPException(status_code=404, detail="SOP not found")
    await db.delete(sop)
    await db.commit()


# --- Users ---

@router.get("/users", response_model=list[UserOut])
async def list_users(
    role: str = None,
    status: str = None,
    org_id: Optional[uuid.UUID] = None,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(User)
        if org_id:
            query = query.where(User.org_id == org_id)
    else:
        query = select(User).where(User.org_id == current_user.org_id)
    if role:
        query = query.where(User.role == role)
    if status:
        query = query.where(User.status == status)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "super_admin" and body.role in ELEVATED_ROLES:
        raise HTTPException(status_code=403, detail="Cannot assign elevated roles")
    data = body.model_dump(exclude={"org_id"})
    password = data.pop("password")
    if current_user.role == "super_admin":
        target_org_id = body.org_id  # may be None for super_admin user
    else:
        target_org_id = current_user.org_id
    user = User(
        org_id=target_org_id,
        password_hash=hash_password(password),
        **data,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(User).where(User.id == user_id)
    else:
        query = select(User).where(and_(User.id == user_id, User.org_id == current_user.org_id))
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.put("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role == "super_admin":
        query = select(User).where(User.id == user_id)
    else:
        query = select(User).where(and_(User.id == user_id, User.org_id == current_user.org_id))
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    data = body.model_dump(exclude_none=True)
    if current_user.role != "super_admin" and data.get("role") in ELEVATED_ROLES:
        raise HTTPException(status_code=403, detail="Cannot assign elevated roles")
    for field, value in data.items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    current_user: User = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    if current_user.role == "super_admin":
        query = select(User).where(User.id == user_id)
    else:
        query = select(User).where(and_(User.id == user_id, User.org_id == current_user.org_id))
    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await db.delete(user)
    await db.commit()
