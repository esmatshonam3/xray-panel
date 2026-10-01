"""Alerting engine: dedupe by fingerprint, escalate, notify.

Delivery channels (best effort, never blocking the request path):
  * Telegram - admin chat IDs from settings
  * Generic webhook - ALERT_WEBHOOK_URL (Slack/Discord/自定义 endpoint)
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import as_utc, utcnow
from app.db.models import Alert, AlertLevel, AlertSource, Node, NodeMetric, NodeStatus, User

log = get_logger(__name__)

DEDUPE_WINDOW_MINUTES = 30
LEVEL_EMOJI = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}


def _fingerprint(code: str, entity_type: Optional[str], entity_id: Optional[int]) -> str:
    raw = f"{code}:{entity_type or '-'}:{entity_id if entity_id is not None else '-'}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:40]


def raise_alert(
    db: Session,
    *,
    code: str,
    title: str,
    message: str = "",
    level: AlertLevel | str = AlertLevel.warning,
    source: AlertSource | str = AlertSource.system,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    meta: Optional[dict[str, Any]] = None,
    notify: bool = True,
    commit: bool = True,
) -> Alert:
    level = AlertLevel(level) if isinstance(level, str) else level
    source = AlertSource(source) if isinstance(source, str) else source
    fp = _fingerprint(code, entity_type, entity_id)
    cutoff = utcnow() - timedelta(minutes=DEDUPE_WINDOW_MINUTES)

    existing = db.execute(
        select(Alert).where(Alert.fingerprint == fp, Alert.is_active.is_(True))
    ).scalar_one_or_none()

    if existing:
        existing.occurrences += 1
        existing.message = message or existing.message
        existing.updated_at = utcnow()
        if existing.created_at and as_utc(existing.created_at) < cutoff:
            existing.notified = False  # re-notify at most once per window
        alert = existing
    else:
        alert = Alert(
            fingerprint=fp,
            code=code,
            title=title,
            message=message,
            level=level,
            source=source,
            entity_type=entity_type,
            entity_id=entity_id,
            meta=meta or {},
            is_active=True,
            occurrences=1,
        )
        db.add(alert)

    if commit:
        db.commit()
        db.refresh(alert)

    if notify and not alert.notified and settings.alerts_enabled:
        delivered = dispatch_alert(alert)
        if delivered:
            alert.notified = True
            if commit:
                db.commit()
    return alert


def resolve_alert(db: Session, code: str, entity_type: Optional[str] = None, entity_id: Optional[int] = None) -> int:
    fp = _fingerprint(code, entity_type, entity_id)
    rows = list(db.execute(select(Alert).where(Alert.fingerprint == fp, Alert.is_active.is_(True))).scalars())
    for row in rows:
        row.is_active = False
        row.resolved_at = utcnow()
    if rows:
        db.commit()
    return len(rows)


def acknowledge(db: Session, alert: Alert, *, user_id: int) -> Alert:
    alert.acknowledged_by_id = user_id
    alert.acknowledged_at = utcnow()
    alert.is_active = False
    alert.resolved_at = utcnow()
    db.commit()
    return alert


# --------------------------------------------------------------------------- #
#  Delivery
# --------------------------------------------------------------------------- #
def _admin_chat_ids() -> list[int]:
    """Admins configured in the panel UI, falling back to the environment."""
    try:
        from app.services import runtime_config

        ids = runtime_config.admin_ids()
        if ids:
            return ids
    except Exception:  # pragma: no cover - DB not ready
        pass
    return settings.telegram_admin_id_list


def _telegram_available() -> bool:
    try:
        from app.services import runtime_config

        return runtime_config.is_enabled()
    except Exception:  # pragma: no cover
        return bool(settings.telegram_enabled and settings.telegram_bot_token)


def dispatch_alert(alert: Alert) -> bool:
    text = (
        f"{LEVEL_EMOJI.get(alert.level.value, '•')} *{alert.title}*\n"
        f"code: `{alert.code}`  level: `{alert.level.value}`\n"
        f"{alert.message}"
    )
    delivered = False

    if _telegram_available():
        try:
            from app.bot.telegram import TelegramClient

            client = TelegramClient()
            for chat_id in _admin_chat_ids():
                if client.send_message(chat_id, text, parse_mode="Markdown"):
                    delivered = True
        except Exception as exc:  # pragma: no cover - network
            log.warning("telegram alert delivery failed", extra={"error": str(exc)})

    if settings.alert_webhook_url:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    settings.alert_webhook_url,
                    json={
                        "level": alert.level.value,
                        "code": alert.code,
                        "title": alert.title,
                        "message": alert.message,
                        "entity_type": alert.entity_type,
                        "entity_id": alert.entity_id,
                        "occurrences": alert.occurrences,
                        "meta": alert.meta,
                    },
                )
                delivered = delivered or resp.status_code < 400
        except httpx.HTTPError as exc:
            log.warning("webhook alert delivery failed", extra={"error": str(exc)})

    return delivered


# --------------------------------------------------------------------------- #
#  Built-in checks (called by the scheduler)
# --------------------------------------------------------------------------- #
def check_node_health(db: Session) -> list[Alert]:
    """Heartbeat staleness + resource pressure for every active node."""
    from app.services.node_client import NodeClient

    created: list[Alert] = []
    if settings.live_proxy_enabled:
        return created
    stale_after = timedelta(seconds=settings.node_heartbeat_stale_seconds)

    for node in db.execute(select(Node).where(Node.is_active.is_(True))).scalars():
        resp = NodeClient(node).health()
        now = utcnow()
        if not resp.ok:
            node.consecutive_failures = (node.consecutive_failures or 0) + 1
            node.last_error = (resp.error or "unreachable")[:500]
            if node.consecutive_failures >= 2:
                node.status = NodeStatus.offline
                created.append(
                    raise_alert(
                        db,
                        code="node.unreachable",
                        title=f"Node {node.name} is unreachable",
                        message=resp.error or "health check failed",
                        level=AlertLevel.critical,
                        source=AlertSource.node,
                        entity_type="node",
                        entity_id=node.id,
                        commit=False,
                    )
                )
            else:
                node.status = NodeStatus.degraded
            db.add(node)
            continue

        data = resp.data
        node.status = NodeStatus.online
        node.consecutive_failures = 0
        node.last_error = None
        node.last_heartbeat_at = now
        node.xray_version = data.get("xray_version") or node.xray_version
        node.cpu_percent = data.get("cpu_percent")
        node.memory_percent = data.get("memory_percent")
        node.disk_percent = data.get("disk_percent")
        node.uptime_seconds = data.get("uptime_seconds")
        node.online_users = data.get("active_users")
        db.add(node)

        db.add(
            NodeMetric(
                node_id=node.id,
                cpu_percent=node.cpu_percent,
                memory_percent=node.memory_percent,
                disk_percent=node.disk_percent,
                online_users=node.online_users,
                net_in_bytes=data.get("net_in_bytes"),
                net_out_bytes=data.get("net_out_bytes"),
                latency_ms=resp.latency_ms,
            )
        )

        resolve_alert(db, "node.unreachable", "node", node.id)

        for metric, threshold, label in (
            ("cpu_percent", settings.alert_cpu_threshold, "CPU"),
            ("memory_percent", settings.alert_memory_threshold, "RAM"),
            ("disk_percent", settings.alert_disk_threshold, "Disk"),
        ):
            value = getattr(node, metric)
            if value is not None and value >= threshold:
                created.append(
                    raise_alert(
                        db,
                        code=f"node.{metric.replace('_percent', '')}_high",
                        title=f"{label} pressure on {node.name}",
                        message=f"{label} usage is {value:.1f}% (threshold {threshold}%)",
                        level=AlertLevel.warning,
                        source=AlertSource.node,
                        entity_type="node",
                        entity_id=node.id,
                        meta={"value": value, "threshold": threshold},
                        commit=False,
                    )
                )
            elif value is not None and value < threshold - 10:
                resolve_alert(db, f"node.{metric.replace('_percent', '')}_high", "node", node.id)

        if data.get("xray_running") is False:
            created.append(
                raise_alert(
                    db,
                    code="node.xray_down",
                    title=f"Xray core is not running on {node.name}",
                    message="The agent reports the Xray process as stopped.",
                    level=AlertLevel.critical,
                    source=AlertSource.node,
                    entity_type="node",
                    entity_id=node.id,
                    commit=False,
                )
            )
        else:
            resolve_alert(db, "node.xray_down", "node", node.id)

    db.commit()
    return created


def check_expiring_services(db: Session, days: int = 3) -> list[Alert]:
    from app.services.provisioning import services_expiring_soon

    created: list[Alert] = []
    for service in services_expiring_soon(db, days=days):
        created.append(
            raise_alert(
                db,
                code="service.expiring",
                title=f"Service #{service.id} expires in {service.days_left} day(s)",
                message=f"user_id={service.user_id} tag={service.email_tag}",
                level=AlertLevel.info,
                source=AlertSource.service,
                entity_type="service",
                entity_id=service.id,
                notify=False,
                commit=False,
            )
        )
    db.commit()
    return created


def prune_metrics(db: Session, days: int = 30) -> int:
    cutoff = utcnow() - timedelta(days=days)
    rows = list(db.execute(select(NodeMetric).where(NodeMetric.recorded_at < cutoff)).scalars())
    for row in rows:
        db.delete(row)
    if rows:
        db.commit()
    return len(rows)
