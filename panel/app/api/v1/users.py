"""User administration (admin scope) + self-service profile endpoints."""
from __future__ import annotations

import uuid as uuid_lib
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, or_, select

from app.api.deps import AdminUser, CurrentUser, DbSession, Paging, StaffUser
from app.core.security import hash_password
from app.db.base import utcnow
from app.db.models import Role, Service, User, UserStatus
from app.schemas import Message, Page, UserCreate, UserOut, UserUpdate
from app.services.audit import audit
from app.services.billing import ensure_referral_code
from app.services.serializers import count_services_by_user, usage_by_user, user_to_out

router = APIRouter(prefix="/users", tags=["users"])


def _load_or_404(db, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("", response_model=Page[UserOut])
def list_users(
    db: DbSession,
    paging: Paging,
    _: StaffUser,
    status_filter: Optional[UserStatus] = None,
    role: Optional[Role] = None,
) -> Page[UserOut]:
    stmt = select(User)
    if paging.q:
        like = f"%{paging.q}%"
        stmt = stmt.where(
            or_(User.username.ilike(like), User.email.ilike(like), User.note.ilike(like))
        )
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    if role:
        stmt = stmt.where(User.role == role)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(stmt.order_by(User.created_at.desc()).limit(paging.size).offset(paging.offset)).scalars()
    )
    ids = [u.id for u in rows]
    counts = count_services_by_user(db, ids)
    usage = usage_by_user(db, ids)
    items = [UserOut.model_validate(user_to_out(u, service_count=counts.get(u.id, 0), total_used_bytes=usage.get(u.id, 0))) for u in rows]
    return Page.build(items, total, paging.page, paging.size)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: DbSession, actor: AdminUser) -> UserOut:
    if db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
    if payload.email and db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    if payload.role in (Role.admin, Role.owner) and actor.role != Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can create admins")

    user = User(
        uuid=str(uuid_lib.uuid4()),
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        status=payload.status,
        note=payload.note,
        balance=payload.balance,
    )
    db.add(user)
    db.flush()
    ensure_referral_code(db, user)
    audit(
        db,
        action="user.create",
        actor_id=actor.id,
        entity_type="user",
        entity_id=user.id,
        meta={"username": user.username, "role": user.role.value},
    )
    db.commit()
    return UserOut.model_validate(user_to_out(user))


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: DbSession, _: StaffUser) -> UserOut:
    user = _load_or_404(db, user_id)
    counts = count_services_by_user(db, [user.id])
    usage = usage_by_user(db, [user.id])
    return UserOut.model_validate(
        user_to_out(user, service_count=counts.get(user.id, 0), total_used_bytes=usage.get(user.id, 0))
    )


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, db: DbSession, actor: AdminUser) -> UserOut:
    user = _load_or_404(db, user_id)
    changes: dict = {}

    if payload.role is not None and payload.role != user.role:
        if actor.role != Role.owner and (payload.role in (Role.admin, Role.owner) or user.role in (Role.admin, Role.owner)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can change staff roles")
        changes["role"] = payload.role.value
        user.role = payload.role

    for field in ("email", "status", "note", "balance"):
        value = getattr(payload, field)
        if value is not None:
            changes[field] = value.value if hasattr(value, "value") else value
            setattr(user, field, value)

    if payload.password:
        user.password_hash = hash_password(payload.password)
        user.must_change_password = True
        changes["password"] = "reset"

    audit(
        db,
        action="user.role_change" if "role" in changes else "user.update",
        actor_id=actor.id,
        entity_type="user",
        entity_id=user.id,
        meta={"changes": changes},
    )
    db.commit()
    return UserOut.model_validate(user_to_out(user))


@router.post("/{user_id}/suspend", response_model=Message)
def suspend_user(user_id: int, db: DbSession, actor: AdminUser) -> Message:
    user = _load_or_404(db, user_id)
    if user.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot suspend yourself")
    user.status = UserStatus.disabled
    services = list(db.execute(select(Service).where(Service.user_id == user.id)).scalars())
    from app.db.models import ServiceStatus
    from app.services.provisioning import set_status

    for service in services:
        if service.status == ServiceStatus.active:
            set_status(db, service, ServiceStatus.disabled, actor_id=actor.id, commit=False)

    audit(db, action="user.update", actor_id=actor.id, entity_type="user", entity_id=user.id, meta={"status": "disabled"})
    db.commit()
    return Message(detail=f"User {user.username} suspended and {len(services)} configs disabled")


@router.post("/{user_id}/activate", response_model=Message)
def activate_user(user_id: int, db: DbSession, actor: AdminUser) -> Message:
    user = _load_or_404(db, user_id)
    user.status = UserStatus.active
    audit(db, action="user.update", actor_id=actor.id, entity_type="user", entity_id=user.id, meta={"status": "active"})
    db.commit()
    return Message(detail=f"User {user.username} activated")


@router.post("/{user_id}/unlock", response_model=Message)
def unlock_user(user_id: int, db: DbSession, actor: AdminUser) -> Message:
    user = _load_or_404(db, user_id)
    user.failed_login_count = 0
    user.locked_until = None
    audit(db, action="user.update", actor_id=actor.id, entity_type="user", entity_id=user.id, meta={"unlocked": True})
    db.commit()
    return Message(detail="Account unlocked")


@router.delete("/{user_id}", response_model=Message)
def delete_user(user_id: int, db: DbSession, actor: AdminUser) -> Message:
    user = _load_or_404(db, user_id)
    if user.id == actor.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete yourself")
    if user.role == Role.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Owner account cannot be deleted")

    from app.db.models import ServiceStatus
    from app.services.provisioning import set_status

    for service in db.execute(select(Service).where(Service.user_id == user.id)).scalars():
        set_status(db, service, ServiceStatus.deleted, actor_id=actor.id, commit=False)

    audit(db, action="user.delete", actor_id=actor.id, entity_type="user", entity_id=user.id, meta={"username": user.username})
    db.delete(user)
    db.commit()
    return Message(detail="User deleted")
