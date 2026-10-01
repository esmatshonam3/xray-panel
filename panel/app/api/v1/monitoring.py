"""Health probes, Prometheus-style metrics and alert management."""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession, StaffUser
from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models import Alert, Node, NodeMetric, NodeStatus, Service, ServiceStatus, User
from app.schemas import AlertCreate, AlertOut, HealthComponent, HealthReport, Message, NodeMetricPoint
from app.services.alerts import acknowledge, raise_alert

router = APIRouter(tags=["monitoring"])
log = get_logger(__name__)

_STARTED_AT = time.time()


# --------------------------------------------------------------------------- #
#  Probes (no auth - Render health checks must be able to reach them)
# --------------------------------------------------------------------------- #
@router.get("/health/live", include_in_schema=False)
def live() -> dict:
    return {"status": "ok", "uptime_seconds": int(time.time() - _STARTED_AT)}


@router.get("/health/ready", include_in_schema=False)
def ready(db: DbSession) -> dict:
    try:
        db.execute(select(func.count(User.id)))
    except Exception as exc:  # pragma: no cover
        log.error("readiness probe failed", extra={"error": str(exc)})
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable")
    return {"status": "ok", "database": "ok"}


@router.get("/health", response_model=HealthReport)
def deep_health(db: DbSession, _: StaffUser) -> HealthReport:
    """Deep health: database, scheduler, connection endpoints and alert backlog."""
    components: list[HealthComponent] = []

    started = time.perf_counter()
    try:
        db.execute(select(func.count(User.id)))
        components.append(
            HealthComponent(name="database", status="ok", latency_ms=round((time.perf_counter() - started) * 1000, 2))
        )
    except Exception as exc:
        components.append(HealthComponent(name="database", status="down", detail=str(exc)[:200]))

    from app.workers.scheduler import scheduler_state

    state = scheduler_state()
    components.append(
        HealthComponent(
            name="scheduler",
            status="ok" if state.get("running") else ("degraded" if settings.enable_scheduler else "ok"),
            detail=state.get("detail"),
        )
    )

    from app.services.provisioning import selectable_nodes

    available_nodes = selectable_nodes(db)
    nodes_total = len(available_nodes)
    nodes_online = sum(node.status == NodeStatus.online for node in available_nodes)
    nodes_down = sum(node.status == NodeStatus.offline for node in available_nodes)
    components.append(
        HealthComponent(
            name="connections",
            status="down" if (nodes_total and nodes_online == 0) else ("degraded" if nodes_down else "ok"),
            detail=f"{nodes_online}/{nodes_total} online",
        )
    )

    active_alerts = db.execute(select(func.count(Alert.id)).where(Alert.is_active.is_(True))).scalar_one()
    components.append(
        HealthComponent(
            name="alerts",
            status="degraded" if active_alerts else "ok",
            detail=f"{active_alerts} active alert(s)",
        )
    )

    if settings.telegram_enabled and settings.telegram_bot_token:
        from app.bot.telegram import TelegramClient

        info = TelegramClient().get_me()
        components.append(
            HealthComponent(
                name="telegram",
                status="ok" if info else "degraded",
                detail=(info or {}).get("username") or "bot not reachable",
            )
        )

    overall = "ok"
    if any(c.status == "down" for c in components):
        overall = "down"
    elif any(c.status == "degraded" for c in components):
        overall = "degraded"

    return HealthReport(
        status=overall,
        version=__import__("app").__version__,
        environment=settings.environment,
        uptime_seconds=int(time.time() - _STARTED_AT),
        checked_at=utcnow(),
        components=components,
        nodes_online=nodes_online,
        nodes_total=nodes_total,
    )


@router.get("/metrics", include_in_schema=False)
def metrics(db: DbSession) -> Response:
    """Minimal Prometheus exposition format - drop-in for Grafana Cloud."""
    lines: list[str] = []

    def emit(name: str, value, labels: Optional[dict] = None) -> None:
        if labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_str}}} {value}")
        else:
            lines.append(f"{name} {value}")

    emit("xpanel_up", 1)
    emit("xpanel_uptime_seconds", int(time.time() - _STARTED_AT))

    by_status = db.execute(select(Service.status, func.count(Service.id)).group_by(Service.status)).all()
    for row in by_status:
        emit("xpanel_services", int(row[1]), {"status": row[0].value if hasattr(row[0], "value") else str(row[0])})

    for node in db.execute(select(Node)).scalars():
        labels = {"node": node.name}
        emit("xpanel_node_up", 1 if node.status == NodeStatus.online else 0, labels)
        emit("xpanel_node_cpu_percent", node.cpu_percent or 0, labels)
        emit("xpanel_node_memory_percent", node.memory_percent or 0, labels)
        emit("xpanel_node_online_users", node.online_users or 0, labels)

    emit("xpanel_users_total", db.execute(select(func.count(User.id))).scalar_one())
    emit("xpanel_alerts_active", db.execute(select(func.count(Alert.id)).where(Alert.is_active.is_(True))).scalar_one())
    emit(
        "xpanel_traffic_bytes_total",
        int(db.execute(select(func.coalesce(func.sum(Service.used_up_bytes + Service.used_down_bytes), 0))).scalar_one()),
    )
    return Response(content="\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


# --------------------------------------------------------------------------- #
#  Alerts
# --------------------------------------------------------------------------- #
@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    db: DbSession,
    _: StaffUser,
    active_only: bool = True,
    limit: int = Query(100, ge=1, le=500),
) -> list[AlertOut]:
    stmt = select(Alert)
    if active_only:
        stmt = stmt.where(Alert.is_active.is_(True))
    rows = list(db.execute(stmt.order_by(Alert.created_at.desc()).limit(limit)).scalars())
    return [AlertOut.model_validate(a) for a in rows]


@router.post("/alerts", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
def create_alert(payload: AlertCreate, db: DbSession, actor: AdminUser) -> AlertOut:
    alert = raise_alert(
        db,
        code=payload.code,
        title=payload.title,
        message=payload.message,
        level=payload.level,
        source=payload.source,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        meta=payload.meta,
    )
    return AlertOut.model_validate(alert)


@router.post("/alerts/{alert_id}/ack", response_model=Message)
def ack_alert(alert_id: int, db: DbSession, actor: AdminUser) -> Message:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    acknowledge(db, alert, user_id=actor.id)
    return Message(detail="Alert acknowledged")


@router.get("/alerts/node/{node_id}/metrics", response_model=list[NodeMetricPoint])
def node_metrics(
    node_id: int,
    db: DbSession,
    _: StaffUser,
    hours: int = Query(24, ge=1, le=720),
) -> list[NodeMetricPoint]:
    from datetime import timedelta

    cutoff = utcnow() - timedelta(hours=hours)
    rows = list(
        db.execute(
            select(NodeMetric)
            .where(NodeMetric.node_id == node_id, NodeMetric.recorded_at >= cutoff)
            .order_by(NodeMetric.recorded_at)
        ).scalars()
    )
    return [
        NodeMetricPoint(
            recorded_at=r.recorded_at,
            cpu_percent=r.cpu_percent,
            memory_percent=r.memory_percent,
            disk_percent=r.disk_percent,
            online_users=r.online_users,
        )
        for r in rows
    ]


@router.get("/status", response_model=dict)
def public_status(db: DbSession) -> dict:
    """Aggregate, non-sensitive status banner for the login page."""
    from app.services.provisioning import selectable_nodes

    available_nodes = selectable_nodes(db)
    nodes_total = len(available_nodes)
    nodes_online = sum(node.status == NodeStatus.online for node in available_nodes)
    active = db.execute(select(func.count(Service.id)).where(Service.status == ServiceStatus.active)).scalar_one()
    return {
        "app": settings.app_name,
        "version": __import__("app").__version__,
        "environment": settings.environment,
        "nodes_total": nodes_total,
        "nodes_online": nodes_online,
        "services_active": active,
        "status": "operational" if nodes_online else ("degraded" if nodes_total else "offline"),
        "checked_at": utcnow().isoformat(),
    }
