"""Public subscription endpoints - the only unauthenticated surface.

Two mount points serve the exact same handlers:

    /sub/<token>                  <- short, user facing, used in configs & bot
    /api/v1/subscription/<token>  <- API-namespaced alias (backwards compatible)

The short path matters: `Service.subscription_url` embeds it, so if the SPA
catch-all route ever wins over these handlers, clients receive the panel's HTML
instead of a config. Route registration order in `main.create_app()` guarantees
the opposite, and `tests/test_subscription.py` locks that behaviour in.

The `sub_token` is a 32-char URL-safe secret, so it acts as a bearer capability.
Rate limiting + strict response caching headers are applied.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import DbSession, rate_limit
from app.core.config import settings
from app.db.base import as_utc
from app.db.models import Service, ServiceStatus
from app.services.xray_links import build_link, build_subscription, format_bytes, qr_png

# Handlers are declared on a prefix-less router and mounted twice at the bottom.
handlers = APIRouter()

_CACHE = "private, max-age=300"


def _resolve(db, token: str) -> Service:
    service = db.execute(select(Service).where(Service.sub_token == token)).unique().scalar_one_or_none()
    if service is None or service.status == ServiceStatus.deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Subscription not found")
    return service


def _guard_usable(service: Service) -> None:
    """Expired / suspended subscriptions return a helpful body, not a 500."""
    if service.is_expired or service.status == ServiceStatus.expired:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Subscription expired - please renew")
    if service.is_quota_exhausted or service.status == ServiceStatus.limited:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Traffic quota exhausted - please renew or top up")
    if service.status == ServiceStatus.disabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Subscription suspended - contact support")


def _siblings(db, service: Service) -> list[Service]:
    """All usable configs of the same user share one subscription URL."""
    rows = list(
        db.execute(
            select(Service)
            .where(Service.user_id == service.user_id, Service.status == ServiceStatus.active)
            .order_by(Service.id)
        )
        .unique()
        .scalars()
    )
    usable = [s for s in rows if s.is_usable]
    return usable or [service]


@handlers.get("/{token}", dependencies=[Depends(rate_limit)])
def get_subscription(
    token: str,
    db: DbSession,
    request: Request,
    fmt: str = Query("base64", alias="format", pattern="^(base64|plain|clash)$"),
) -> Response:
    service = _resolve(db, token)
    _guard_usable(service)
    body = build_subscription(_siblings(db, service), fmt=fmt)

    media = "text/yaml" if fmt == "clash" else "text/plain"
    headers = {
        "Cache-Control": _CACHE,
        "Profile-Title": settings.app_name,
        "Profile-Update-Interval": "12",
        "Subscription-Userinfo": (
            f"upload={service.used_up_bytes}; download={service.used_down_bytes}; "
            f"total={service.traffic_limit_bytes}; "
            f"expire={int(as_utc(service.expires_at).timestamp()) if service.expires_at else 0}"
        ),
    }
    return Response(content=body, media_type=media, headers=headers)


@handlers.get("/{token}/clash.yaml", dependencies=[Depends(rate_limit)])
def get_clash(token: str, db: DbSession, request: Request) -> Response:
    service = _resolve(db, token)
    _guard_usable(service)
    body = build_subscription(_siblings(db, service), fmt="clash")
    return Response(content=body, media_type="text/yaml", headers={"Cache-Control": _CACHE})


@handlers.get("/{token}/info", dependencies=[Depends(rate_limit)])
def subscription_info(token: str, db: DbSession, request: Request) -> dict:
    """Status payload used by the bot and the web UI (no secrets beyond the link)."""
    service = _resolve(db, token)
    return {
        "label": service.label,
        "status": service.status.value,
        "protocol": service.protocol.value,
        "node": service.node.name if service.node else None,
        "expires_at": service.expires_at.isoformat() if service.expires_at else None,
        "days_left": service.days_left,
        "used_bytes": service.used_bytes,
        "limit_bytes": service.traffic_limit_bytes,
        "used_human": format_bytes(service.used_bytes),
        "limit_human": format_bytes(service.traffic_limit_bytes) if service.traffic_limit_bytes else "unlimited",
        "usage_percent": service.usage_percent,
        "usable": service.is_usable,
        "link": build_link(service) if service.is_usable else None,
        "subscription_url": service.subscription_url,
    }


@handlers.get("/{token}/qr.png", dependencies=[Depends(rate_limit)])
def subscription_qr(token: str, db: DbSession, request: Request, size: int = Query(8, ge=4, le=20)) -> Response:
    service = _resolve(db, token)
    _guard_usable(service)
    return Response(
        content=qr_png(build_link(service), box_size=size),
        media_type="image/png",
        headers={"Cache-Control": _CACHE},
    )


# --------------------------------------------------------------------------- #
#  Mount points
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/subscription", tags=["subscription"])
router.include_router(handlers)

# Mounted at the application root (no /api/v1 prefix) so config URLs stay short.
short_router = APIRouter(prefix="/sub", tags=["subscription"], include_in_schema=False)
short_router.include_router(handlers)
