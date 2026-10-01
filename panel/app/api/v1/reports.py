"""Usage reports, dashboard aggregates and CSV exports."""
from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Query, Response
from sqlalchemy import case, func, select

from app.api.deps import CurrentUser, DbSession, StaffUser
from app.core.config import settings
from app.db.base import utcnow
from app.db.models import (
    Alert,
    Node,
    NodeStatus,
    Payment,
    PaymentStatus,
    Service,
    ServiceStatus,
    TrafficDaily,
    User,
    UserStatus,
)
from app.schemas import DashboardStats, TopConsumer, TrafficPoint

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/dashboard", response_model=DashboardStats)
def dashboard(db: DbSession, user: CurrentUser) -> DashboardStats:
    today = date.today()
    month_start = today.replace(day=1)
    now = utcnow()

    if user.is_staff:
        users_total = db.execute(select(func.count(User.id))).scalar_one()
        users_active = db.execute(
            select(func.count(User.id)).where(User.status == UserStatus.active)
        ).scalar_one()
        users_new = db.execute(
            select(func.count(User.id)).where(func.date(User.created_at) == today.isoformat())
        ).scalar_one()
        services_total = db.execute(
            select(func.count(Service.id)).where(Service.status != ServiceStatus.deleted)
        ).scalar_one()
        services_active = db.execute(
            select(func.count(Service.id)).where(Service.status == ServiceStatus.active)
        ).scalar_one()
        expiring = db.execute(
            select(func.count(Service.id)).where(
                Service.status == ServiceStatus.active,
                Service.expires_at.isnot(None),
                Service.expires_at <= now + timedelta(days=7),
            )
        ).scalar_one()
        nodes_total = db.execute(select(func.count(Node.id)).where(Node.is_active.is_(True))).scalar_one()
        nodes_online = db.execute(
            select(func.count(Node.id)).where(Node.is_active.is_(True), Node.status == NodeStatus.online)
        ).scalar_one()
        traffic_today = db.execute(
            select(func.coalesce(func.sum(TrafficDaily.up_bytes + TrafficDaily.down_bytes), 0)).where(
                TrafficDaily.day == today
            )
        ).scalar_one()
        traffic_month = db.execute(
            select(func.coalesce(func.sum(TrafficDaily.up_bytes + TrafficDaily.down_bytes), 0)).where(
                TrafficDaily.day >= month_start
            )
        ).scalar_one()
        revenue = db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.paid, func.date(Payment.paid_at) >= month_start.isoformat()
            )
        ).scalar_one()
        alerts_active = db.execute(select(func.count(Alert.id)).where(Alert.is_active.is_(True))).scalar_one()
        pending = db.execute(
            select(func.count(Payment.id)).where(
                Payment.status.in_([PaymentStatus.pending, PaymentStatus.awaiting_review])
            )
        ).scalar_one()
    else:
        scope = Service.user_id == user.id
        users_total = users_active = users_new = 1
        services_total = db.execute(
            select(func.count(Service.id)).where(scope, Service.status != ServiceStatus.deleted)
        ).scalar_one()
        services_active = db.execute(
            select(func.count(Service.id)).where(scope, Service.status == ServiceStatus.active)
        ).scalar_one()
        expiring = db.execute(
            select(func.count(Service.id)).where(
                scope, Service.expires_at.isnot(None), Service.expires_at <= now + timedelta(days=7)
            )
        ).scalar_one()
        nodes_total = nodes_online = 0
        service_ids = select(Service.id).where(scope).scalar_subquery()
        traffic_today = db.execute(
            select(func.coalesce(func.sum(TrafficDaily.up_bytes + TrafficDaily.down_bytes), 0)).where(
                TrafficDaily.service_id.in_(service_ids), TrafficDaily.day == today
            )
        ).scalar_one()
        traffic_month = db.execute(
            select(func.coalesce(func.sum(TrafficDaily.up_bytes + TrafficDaily.down_bytes), 0)).where(
                TrafficDaily.service_id.in_(service_ids), TrafficDaily.day >= month_start
            )
        ).scalar_one()
        revenue = db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.user_id == user.id, Payment.status == PaymentStatus.paid
            )
        ).scalar_one()
        alerts_active = 0
        pending = db.execute(
            select(func.count(Payment.id)).where(
                Payment.user_id == user.id,
                Payment.status.in_([PaymentStatus.pending, PaymentStatus.awaiting_review]),
            )
        ).scalar_one()

    return DashboardStats(
        users_total=users_total,
        users_active=users_active,
        users_new_today=users_new,
        services_total=services_total,
        services_active=services_active,
        services_expiring_7d=expiring,
        nodes_total=nodes_total,
        nodes_online=nodes_online,
        traffic_today_bytes=int(traffic_today or 0),
        traffic_month_bytes=int(traffic_month or 0),
        revenue_month=round(float(revenue or 0), 2),
        currency=settings.default_currency,
        alerts_active=alerts_active,
        pending_payments=pending,
    )


@router.get("/traffic", response_model=list[TrafficPoint])
def traffic_series(
    db: DbSession,
    user: CurrentUser,
    days: int = Query(30, ge=1, le=365),
    user_id: Optional[int] = None,
    service_id: Optional[int] = None,
) -> list[TrafficPoint]:
    start = date.today() - timedelta(days=days - 1)
    stmt = (
        select(
            TrafficDaily.day,
            func.coalesce(func.sum(TrafficDaily.up_bytes), 0),
            func.coalesce(func.sum(TrafficDaily.down_bytes), 0),
        )
        .where(TrafficDaily.day >= start)
        .group_by(TrafficDaily.day)
        .order_by(TrafficDaily.day)
    )
    if not user.is_staff:
        stmt = stmt.where(TrafficDaily.user_id == user.id)
    elif user_id:
        stmt = stmt.where(TrafficDaily.user_id == user_id)
    if service_id:
        stmt = stmt.where(TrafficDaily.service_id == service_id)

    rows = {row[0]: (int(row[1]), int(row[2])) for row in db.execute(stmt).all()}
    out: list[TrafficPoint] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        up, down = rows.get(day, (0, 0))
        out.append(TrafficPoint(day=day.isoformat(), up_bytes=up, down_bytes=down, total_bytes=up + down))
    return out


@router.get("/top-consumers", response_model=list[TopConsumer])
def top_consumers(db: DbSession, user: CurrentUser, limit: int = Query(10, ge=1, le=100)) -> list[TopConsumer]:
    stmt = (
        select(Service)
        .where(Service.status != ServiceStatus.deleted)
        .order_by((Service.used_up_bytes + Service.used_down_bytes).desc())
        .limit(limit)
    )
    if not user.is_staff:
        stmt = stmt.where(Service.user_id == user.id)
    rows = list(db.execute(stmt).unique().scalars())
    return [
        TopConsumer(
            service_id=s.id,
            username=s.user.username if s.user else "?",
            label=s.label,
            used_bytes=s.used_bytes,
            limit_bytes=s.traffic_limit_bytes,
            usage_percent=s.usage_percent,
        )
        for s in rows
    ]


@router.get("/services.csv")
def export_services(db: DbSession, _: StaffUser) -> Response:
    rows = list(
        db.execute(select(Service).where(Service.status != ServiceStatus.deleted).order_by(Service.id))
        .unique()
        .scalars()
    )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id", "user", "label", "protocol", "node", "inbound", "status",
            "expires_at", "days_left", "used_bytes", "limit_bytes", "usage_percent",
            "email_tag", "sub_token", "last_connected_at",
        ]
    )
    for s in rows:
        writer.writerow(
            [
                s.id,
                s.user.username if s.user else "",
                s.label,
                s.protocol.value,
                s.node.name if s.node else "",
                s.inbound.tag if s.inbound else "",
                s.status.value,
                s.expires_at.isoformat() if s.expires_at else "",
                s.days_left if s.days_left is not None else "",
                s.used_bytes,
                s.traffic_limit_bytes,
                s.usage_percent,
                s.email_tag,
                s.sub_token,
                s.last_connected_at.isoformat() if s.last_connected_at else "",
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="services-{date.today().isoformat()}.csv"'},
    )


@router.get("/revenue")
def revenue_series(db: DbSession, _: StaffUser, days: int = Query(30, ge=1, le=365)) -> list[dict]:
    start = date.today() - timedelta(days=days - 1)
    rows = db.execute(
        select(
            func.date(Payment.paid_at).label("day"),
            func.coalesce(func.sum(Payment.amount), 0),
            func.count(Payment.id),
        )
        .where(Payment.status == PaymentStatus.paid, func.date(Payment.paid_at) >= start.isoformat())
        .group_by(func.date(Payment.paid_at))
        .order_by(func.date(Payment.paid_at))
    ).all()
    return [{"day": str(r[0]), "amount": round(float(r[1] or 0), 2), "count": int(r[2])} for r in rows]


@router.get("/summary")
def summary(db: DbSession, _: StaffUser) -> dict:
    """Aggregate view used by the bot's admin report."""
    today = date.today()
    by_status = {
        row[0].value if hasattr(row[0], "value") else row[0]: int(row[1])
        for row in db.execute(select(Service.status, func.count(Service.id)).group_by(Service.status)).all()
    }
    by_protocol = {
        row[0].value if hasattr(row[0], "value") else row[0]: int(row[1])
        for row in db.execute(select(Service.protocol, func.count(Service.id)).group_by(Service.protocol)).all()
    }
    by_node = [
        {"node": row[0], "services": int(row[1])}
        for row in db.execute(
            select(Node.name, func.count(Service.id)).join(Service, Service.node_id == Node.id).group_by(Node.name)
        ).all()
    ]
    total_traffic = db.execute(
        select(func.coalesce(func.sum(Service.used_up_bytes + Service.used_down_bytes), 0))
    ).scalar_one()
    paid_month = db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.status == PaymentStatus.paid, func.date(Payment.paid_at) >= today.replace(day=1).isoformat()
        )
    ).scalar_one()
    return {
        "services_by_status": by_status,
        "services_by_protocol": by_protocol,
        "services_by_node": by_node,
        "total_traffic_bytes": int(total_traffic or 0),
        "revenue_this_month": round(float(paid_month or 0), 2),
        "generated_at": utcnow().isoformat(),
    }
