"""Shared FastAPI dependencies: auth, RBAC, pagination, request context.

NOTE: this module deliberately does NOT use `from __future__ import annotations`.
FastAPI resolves class-based dependency signatures (see `Pagination`) eagerly,
and PEP 563 string annotations make `Optional[...]` query params unresolvable,
which breaks OpenAPI generation for every route that depends on them.
"""

from dataclasses import dataclass
from typing import Annotated, Optional

import jwt
from fastapi import Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import request_id_ctx
from app.core.ratelimit import SlidingWindowLimiter
from app.core.security import decode_token, hash_token
from app.db.models import Role, User, UserStatus
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False, description="Panel JWT access token")

api_limiter = SlidingWindowLimiter(settings.api_rate_limit_per_minute, 60)


@dataclass
class RequestContext:
    request: Request
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: str = "-"


def client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else None


def get_context(request: Request) -> RequestContext:
    return RequestContext(
        request=request,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        request_id=request_id_ctx.get(),
    )


def rate_limit(request: Request) -> None:
    key = f"{client_ip(request) or 'unknown'}:{request.url.path}"
    allowed, retry_after = api_limiter.check(key)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests",
            headers={"Retry-After": str(retry_after)},
        )


def _user_from_token(db: Session, token: str) -> User:
    try:
        payload = decode_token(token, expected_type="access")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found")
    if user.status == UserStatus.disabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    return user


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer)],
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
) -> User:
    """Accepts either a JWT bearer token or a long-lived API key (for automation)."""
    if credentials and credentials.credentials:
        return _user_from_token(db, credentials.credentials)

    if x_api_key:
        digest = hash_token(x_api_key)
        user = db.query(User).filter(User.api_key_hash == digest).first()
        if user and user.status != UserStatus.disabled:
            return user
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")

    raise HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_optional_user(
    db: Annotated[Session, Depends(get_db)],
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer)],
) -> Optional[User]:
    if not credentials or not credentials.credentials:
        return None
    try:
        return _user_from_token(db, credentials.credentials)
    except HTTPException:
        return None


def require_roles(*roles: Role):
    allowed = set(roles)

    def _guard(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return _guard


require_admin = require_roles(Role.admin, Role.owner)
require_staff = require_roles(Role.support, Role.admin, Role.owner)
require_owner = require_roles(Role.owner)


class Pagination:
    def __init__(
        self,
        page: int = Query(1, ge=1),
        size: int = Query(20, ge=1, le=200),
        q: Optional[str] = Query(None, max_length=128),
    ) -> None:
        self.page = page
        self.size = size
        self.q = q

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
StaffUser = Annotated[User, Depends(require_staff)]
OwnerUser = Annotated[User, Depends(require_owner)]
Ctx = Annotated[RequestContext, Depends(get_context)]
Paging = Annotated[Pagination, Depends(Pagination)]
RateLimited = Depends(rate_limit)
