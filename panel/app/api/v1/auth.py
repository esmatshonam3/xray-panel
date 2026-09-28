"""Authentication: login, refresh, password change, TOTP, API keys."""
from __future__ import annotations

from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from app.api.deps import Ctx, CurrentUser, DbSession, rate_limit
from app.core.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import SlidingWindowLimiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_token,
    hash_password,
    hash_token,
    new_totp_secret,
    totp_uri,
    verify_password,
    verify_totp,
)
from app.db.base import utcnow
from app.db.models import Role, User, UserStatus
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    Message,
    RegisterRequest,
    TokenPair,
    TotpSetupOut,
    UserMe,
)
from app.services.audit import audit
from app.services.billing import apply_referral, ensure_referral_code

router = APIRouter(prefix="/auth", tags=["auth"])
log = get_logger(__name__)

login_limiter = SlidingWindowLimiter(settings.login_max_attempts, settings.login_window_seconds)


def _issue_tokens(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(str(user.id), role=user.role.value, username=user.username),
        refresh_token=create_refresh_token(str(user.id)),
        expires_in=settings.access_token_ttl_minutes * 60,
    )


@router.post("/login", response_model=TokenPair, dependencies=[Depends(rate_limit)])
def login(payload: LoginRequest, db: DbSession, ctx: Ctx) -> TokenPair:
    key = f"login:{ctx.ip}:{payload.username.lower()}"
    allowed, retry_after = login_limiter.check(key)
    if not allowed:
        audit(
            db,
            action="auth.login_failed",
            actor_type="user",
            actor_label=payload.username,
            status="throttled",
            ip_address=ctx.ip,
            meta={"reason": "rate_limit"},
            commit=True,
        )
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many attempts, retry in {retry_after}s",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_max_attempts:
                user.locked_until = utcnow() + timedelta(seconds=settings.login_window_seconds)
            db.commit()
        audit(
            db,
            action="auth.login_failed",
            actor_id=user.id if user else None,
            actor_type="user",
            actor_label=payload.username,
            status="failure",
            ip_address=ctx.ip,
            user_agent=ctx.user_agent,
            meta={"reason": "bad_credentials"},
            commit=True,
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    if user.locked_until and user.locked_until > utcnow():
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked, try again later")
    if user.status == UserStatus.disabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    if user.totp_secret and not verify_totp(user.totp_secret, payload.totp_code or ""):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid 2FA code")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    user.last_login_ip = ctx.ip
    db.commit()

    login_limiter.reset(key)
    ensure_referral_code(db, user)
    audit(
        db,
        action="auth.login",
        actor_id=user.id,
        actor_type="user",
        actor_label=user.username,
        ip_address=ctx.ip,
        user_agent=ctx.user_agent,
        commit=True,
    )
    return _issue_tokens(user)


@router.post("/register", response_model=TokenPair, dependencies=[Depends(rate_limit)])
def register(payload: RegisterRequest, db: DbSession, ctx: Ctx) -> TokenPair:
    exists = db.execute(
        select(User).where(User.username == payload.username)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
    if payload.email and db.execute(select(User).where(User.email == payload.email)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    import uuid as uuid_lib

    user = User(
        uuid=str(uuid_lib.uuid4()),
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=Role.user,
        status=UserStatus.active,
    )
    db.add(user)
    db.flush()
    ensure_referral_code(db, user)
    if payload.referral_code:
        apply_referral(db, user, payload.referral_code)

    audit(
        db,
        action="user.create",
        actor_id=user.id,
        actor_type="user",
        actor_label=user.username,
        entity_type="user",
        entity_id=user.id,
        ip_address=ctx.ip,
        meta={"self_registration": True},
    )
    db.commit()
    return _issue_tokens(user)


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: dict, db: DbSession) -> TokenPair:
    token = (payload or {}).get("refresh_token", "")
    try:
        claims = decode_token(token, expected_type="refresh")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    user = db.get(User, int(claims["sub"]))
    if user is None or user.status == UserStatus.disabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account unavailable")
    return _issue_tokens(user)


@router.post("/logout", response_model=Message)
def logout(user: CurrentUser, db: DbSession, ctx: Ctx) -> Message:
    audit(
        db,
        action="auth.logout",
        actor_id=user.id,
        actor_type="user",
        actor_label=user.username,
        ip_address=ctx.ip,
        commit=True,
    )
    return Message(detail="Logged out")


@router.get("/me", response_model=UserMe)
def me(user: CurrentUser) -> UserMe:
    return UserMe.model_validate(
        {
            "id": user.id,
            "uuid": user.uuid,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "status": user.status,
            "balance": user.balance,
            "telegram_id": user.telegram_id,
            "referral_code": user.referral_code,
            "totp_enabled": bool(user.totp_secret),
            "created_at": user.created_at,
        }
    )


@router.post("/change-password", response_model=Message)
def change_password(payload: ChangePasswordRequest, user: CurrentUser, db: DbSession, ctx: Ctx) -> Message:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    audit(
        db,
        action="user.password_change",
        actor_id=user.id,
        actor_type="user",
        actor_label=user.username,
        entity_type="user",
        entity_id=user.id,
        ip_address=ctx.ip,
        commit=True,
    )
    return Message(detail="Password updated")


@router.post("/totp/setup", response_model=TotpSetupOut)
def totp_setup(user: CurrentUser, db: DbSession) -> TotpSetupOut:
    secret = new_totp_secret()
    user.totp_secret = secret
    db.commit()
    return TotpSetupOut(secret=secret, otpauth_uri=totp_uri(secret, user.username))


@router.post("/totp/enable", response_model=Message)
def totp_enable(code: str, user: CurrentUser, db: DbSession) -> Message:
    if not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Run /auth/totp/setup first")
    if not verify_totp(user.totp_secret, code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid code")
    audit(db, action="user.2fa_enabled", actor_id=user.id, entity_type="user", entity_id=user.id, commit=True)
    return Message(detail="2FA enabled")


@router.post("/totp/disable", response_model=Message)
def totp_disable(user: CurrentUser, db: DbSession) -> Message:
    user.totp_secret = None
    audit(db, action="user.2fa_disabled", actor_id=user.id, entity_type="user", entity_id=user.id, commit=True)
    return Message(detail="2FA disabled")


@router.post("/api-key", response_model=dict)
def rotate_api_key(user: CurrentUser, db: DbSession) -> dict:
    raw = generate_token(32, prefix="xp_")
    user.api_key_hash = hash_token(raw)
    audit(db, action="user.api_key_rotate", actor_id=user.id, entity_type="user", entity_id=user.id, commit=True)
    return {"api_key": raw, "warning": "Store it now - it is not shown again."}
