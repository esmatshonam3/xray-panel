"""Provisioning & lifecycle: create, renew, suspend, delete, sync, quota, expiry.

This is the single place that mutates the Xray data plane, so every state
transition is funnelled through here and mirrored into the audit log.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import generate_sub_token, generate_uuid
from app.db.base import as_utc, utcnow
from app.db.models import (
    Inbound,
    Node,
    NodeStatus,
    Plan,
    Protocol,
    Service,
    ServiceStatus,
    TrafficDaily,
    User,
)
from app.services.audit import audit
from app.services.node_client import NodeClient, build_user_payload

log = get_logger(__name__)


class ProvisioningError(Exception):
    """Raised when a service cannot be provisioned for a business reason."""


# --------------------------------------------------------------------------- #
#  Inbound selection
# --------------------------------------------------------------------------- #
def eligible_inbounds(
    db: Session,
    plan: Optional[Plan],
    *,
    node_id: Optional[int] = None,
    inbound_id: Optional[int] = None,
) -> list[Inbound]:
    stmt = (
        select(Inbound)
        .join(Node, Inbound.node_id == Node.id)
        .where(Inbound.is_active.is_(True), Node.is_active.is_(True))
    )
    if inbound_id:
        stmt = stmt.where(Inbound.id == inbound_id)
    if node_id:
        stmt = stmt.where(Inbound.node_id == node_id)
    if settings.live_proxy_enabled:
        # Node is already joined above. Rejoining the same table by name
        # produces duplicate JOIN nodes and ambiguous columns on SQLite.
        stmt = stmt.where(Node.name == "panel-websocket-relay")
    if plan:
        if plan.inbound_ids and not settings.live_proxy_enabled:
            stmt = stmt.where(Inbound.id.in_(plan.inbound_ids))
        if plan.allowed_protocols and not settings.live_proxy_enabled:
            stmt = stmt.where(Inbound.protocol.in_([Protocol(p) for p in plan.allowed_protocols]))
        if plan.node_group:
            # node tags are JSON; do a portable filter in Python afterwards
            pass
    candidates = list(db.execute(stmt).unique().scalars())

    # The built-in relay accepts only VLESS over WebSocket. Keep legacy
    # node-agent modes available for non-Railway installations.
    candidates = [inbound for inbound in candidates if _can_advertise(inbound)]

    if plan and plan.node_group and not settings.live_proxy_enabled:
        candidates = [i for i in candidates if plan.node_group in (i.node.tags or [])]

    # Capacity check + ordering: default inbounds first, then lightest loaded.
    def load(inbound: Inbound) -> int:
        return db.execute(
            select(func.count(Service.id)).where(
                Service.inbound_id == inbound.id, Service.status != ServiceStatus.deleted
            )
        ).scalar_one()

    usable: list[tuple[int, int, Inbound]] = []
    for inbound in candidates:
        node = inbound.node
        current = load(inbound)
        if node.max_services and current >= node.max_services:
            continue
        usable.append((0 if inbound.is_default else 1, current, inbound))

    usable.sort(key=lambda item: (item[0], item[1], -item[2].sort_order))
    return [item[2] for item in usable]


def _can_advertise(inbound: Inbound) -> bool:
    if not settings.live_proxy_enabled:
        return True
    return uses_live_proxy(inbound)


def uses_live_proxy(inbound: Inbound) -> bool:
    return bool(
        settings.live_proxy_enabled
        and inbound.protocol == Protocol.vless
        and inbound.transport.value == "ws"
        and inbound.security.value in ("none", "tls")
    )


def selectable_nodes(db: Session, plan: Optional[Plan] = None) -> list[Node]:
    """Return nodes that currently have an inbound usable for this plan."""
    inbounds = eligible_inbounds(db, plan)
    node_ids = {inbound.node_id for inbound in inbounds}
    return list(
        db.execute(
            select(Node).where(Node.id.in_(node_ids), Node.is_active.is_(True)).order_by(Node.region, Node.name)
        ).scalars()
    ) if node_ids else []


def pick_inbound(
    db: Session,
    plan: Optional[Plan],
    *,
    node_id: Optional[int] = None,
    inbound_id: Optional[int] = None,
) -> Inbound:
    options = eligible_inbounds(db, plan, node_id=node_id, inbound_id=inbound_id)
    if not options:
        raise ProvisioningError(
            "No available inbound matches this plan (check node health, capacity and protocol filters)."
        )
    return options[0]


def _make_email_tag(user: User, plan: Optional[Plan]) -> str:
    plan_code = (plan.code if plan else "custom").lower()[:12]
    return f"{user.username}.{plan_code}.{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
#  Create / renew / mutate
# --------------------------------------------------------------------------- #
def create_service(
    db: Session,
    *,
    user: User,
    plan: Optional[Plan] = None,
    inbound: Optional[Inbound] = None,
    node_id: Optional[int] = None,
    duration_days: Optional[int] = None,
    traffic_gb: Optional[float] = None,
    connection_limit: int = 0,
    ip_limit: int = 0,
    speed_limit_mbps: float = 0,
    expires_at: Optional[datetime] = None,
    label: Optional[str] = None,
    note: Optional[str] = None,
    auto_renew: bool = False,
    actor_id: Optional[int] = None,
    commit: bool = True,
) -> Service:
    if plan is None and not inbound and not node_id:
        raise ProvisioningError("A plan or an explicit inbound/node is required.")

    inbound = inbound or pick_inbound(db, plan, node_id=node_id)

    days = duration_days or (plan.duration_days if plan else 30)
    limit_gb = traffic_gb if traffic_gb is not None else (plan.traffic_gb if plan else 0.0)

    service = Service(
        user_id=user.id,
        plan_id=plan.id if plan else None,
        node_id=inbound.node_id,
        inbound_id=inbound.id,
        label=label or (plan.name if plan else inbound.remark or inbound.tag),
        protocol=inbound.protocol,
        uuid=generate_uuid() if inbound.protocol in (Protocol.vless, Protocol.vmess) else generate_sub_token(),
        email_tag=_make_email_tag(user, plan),
        sub_token=generate_sub_token(),
        flow=inbound.flow if inbound.protocol == Protocol.vless else None,
        ss_method=inbound.ss_method if inbound.protocol == Protocol.shadowsocks else None,
        status=ServiceStatus.active,
        started_at=utcnow(),
        expires_at=expires_at or (utcnow() + timedelta(days=days)),
        auto_renew=auto_renew,
        traffic_limit_bytes=int(limit_gb * 1024 ** 3) if limit_gb else 0,
        note=_encode_relay_limits(note, connection_limit, ip_limit, speed_limit_mbps) if uses_live_proxy(inbound) else note,
    )
    db.add(service)
    db.flush()

    ok, error = push_service(db, service)
    service.is_synced = ok
    service.sync_error = error

    audit(
        db,
        action="service.create",
        actor_id=actor_id,
        actor_type="user" if actor_id else "system",
        entity_type="service",
        entity_id=service.id,
        meta={
            "user_id": user.id,
            "plan_id": plan.id if plan else None,
            "inbound": inbound.tag,
            "node": inbound.node.name,
            "synced": ok,
        },
    )
    if commit:
        db.commit()
    log.info("service provisioned", extra={"service_id": service.id, "user": user.username, "synced": ok})
    return service


def _encode_relay_limits(note: Optional[str], connection_limit: int, ip_limit: int, speed_limit_mbps: float) -> Optional[str]:
    """Persist optional relay limits in existing notes without a schema migration."""
    data = {
        "connection_limit": max(int(connection_limit or 0), 0),
        "ip_limit": max(int(ip_limit or 0), 0),
        "speed_limit_bytes": max(int((speed_limit_mbps or 0) * 1_000_000 / 8), 0),
    }
    metadata = " ".join(f"{key}={value}" for key, value in data.items())
    return f"{(note or '').strip()}\n{metadata}".strip()


def renew_service(
    db: Session,
    service: Service,
    *,
    days: int,
    reset_traffic: bool = False,
    add_traffic_gb: float = 0.0,
    actor_id: Optional[int] = None,
    commit: bool = True,
) -> Service:
    current_expiry = as_utc(service.expires_at)
    base = current_expiry if (current_expiry and current_expiry > utcnow()) else utcnow()
    service.expires_at = base + timedelta(days=days)

    if reset_traffic:
        service.used_up_bytes = 0
        service.used_down_bytes = 0
        service.last_raw_up = 0
        service.last_raw_down = 0
    if add_traffic_gb:
        service.traffic_limit_bytes = max(
            service.traffic_limit_bytes + int(add_traffic_gb * 1024 ** 3), 0
        )

    # Renewal un-limits a quota-exhausted or expired service.
    if service.status in (ServiceStatus.expired, ServiceStatus.limited):
        service.status = ServiceStatus.active
    if service.status == ServiceStatus.active:
        ok, error = push_service(db, service)
        service.is_synced = ok
        service.sync_error = error

    audit(
        db,
        action="service.renew",
        actor_id=actor_id,
        entity_type="service",
        entity_id=service.id,
        meta={"days": days, "reset_traffic": reset_traffic, "add_traffic_gb": add_traffic_gb},
    )
    if commit:
        db.commit()
    return service


def set_status(
    db: Session,
    service: Service,
    status: ServiceStatus,
    *,
    actor_id: Optional[int] = None,
    commit: bool = True,
) -> Service:
    service.status = status
    if status in (ServiceStatus.disabled, ServiceStatus.expired, ServiceStatus.limited, ServiceStatus.deleted):
        ok, error = remove_service(db, service)
    else:
        ok, error = push_service(db, service)
    service.is_synced = ok
    service.sync_error = error

    audit(
        db,
        action=f"service.{status.value}",
        actor_id=actor_id,
        entity_type="service",
        entity_id=service.id,
        meta={"synced": ok, "error": error},
    )
    if commit:
        db.commit()
    return service


def delete_service(db: Session, service: Service, *, actor_id: Optional[int] = None, commit: bool = True) -> None:
    remove_service(db, service)
    service.status = ServiceStatus.deleted
    audit(
        db,
        action="service.delete",
        actor_id=actor_id,
        entity_type="service",
        entity_id=service.id,
        meta={"email": service.email_tag},
    )
    if commit:
        db.commit()


# --------------------------------------------------------------------------- #
#  Data-plane push / pull
# --------------------------------------------------------------------------- #
def push_service(db: Session, service: Service) -> tuple[bool, Optional[str]]:
    """Add (or re-enable) a single user on its node. Returns (ok, error)."""
    if not service.is_usable and service.status != ServiceStatus.active:
        return remove_service(db, service)

    if uses_live_proxy(service.inbound):
        service.last_synced_at = utcnow()
        return True, None

    client = NodeClient(service.node)
    payload = build_user_payload(service)
    payload["enable"] = service.is_usable
    resp = client.add_user(service.inbound.tag, payload, protocol=service.protocol.value)
    if not resp.ok:
        _record_node_failure(db, service.node, resp.error)
        return False, resp.error
    _record_node_success(db, service.node, resp)
    return True, None


def remove_service(db: Session, service: Service) -> tuple[bool, Optional[str]]:
    if uses_live_proxy(service.inbound):
        return True, None
    client = NodeClient(service.node)
    resp = client.remove_user(service.inbound.tag, service.email_tag)
    if not resp.ok and resp.status_code not in (404, 400):
        _record_node_failure(db, service.node, resp.error)
        return False, resp.error
    _record_node_success(db, service.node, resp)
    return True, None


def sync_inbound(db: Session, inbound: Inbound, *, actor_id: Optional[int] = None) -> tuple[bool, str]:
    """Full desired-state apply: the node ends up with exactly these users."""
    services: Sequence[Service] = list(
        db.execute(
            select(Service).where(
                Service.inbound_id == inbound.id, Service.status != ServiceStatus.deleted
            )
        ).unique().scalars()
    )
    if uses_live_proxy(inbound):
        now = utcnow()
        for service in services:
            service.is_synced = True
            service.sync_error = None
            service.last_synced_at = now
        db.commit()
        return True, f"{len(services)} users active on built-in WebSocket relay"
    users = [build_user_payload(s) for s in services]
    client = NodeClient(inbound.node)
    resp = client.apply_users(inbound.tag, users, protocol=inbound.protocol.value)
    if not resp.ok:
        _record_node_failure(db, inbound.node, resp.error)
        return False, resp.error or "unknown node error"

    now = utcnow()
    for service in services:
        service.is_synced = True
        service.sync_error = None
        service.last_synced_at = now
    _record_node_success(db, inbound.node, resp)
    audit(
        db,
        action="inbound.sync",
        actor_id=actor_id,
        entity_type="inbound",
        entity_id=inbound.id,
        meta={"users": len(users), "node": inbound.node.name},
    )
    db.commit()
    return True, f"{len(users)} users applied"


def inbound_spec(inbound: Inbound) -> dict[str, Any]:
    """Serialise an Inbound row into the node agent's inbound descriptor."""
    return {
        "tag": inbound.tag,
        "protocol": inbound.protocol.value,
        "port": inbound.port,
        "listen": inbound.listen or "0.0.0.0",
        "transport": inbound.transport.value,
        "security": inbound.security.value,
        "sni": inbound.sni,
        "alpn": inbound.alpn or [],
        "path": inbound.path,
        "host_header": inbound.host_header,
        "service_name": inbound.service_name,
        "flow": inbound.flow,
        "ss_method": inbound.ss_method,
        "ss_password": (inbound.extra or {}).get("ss_password"),
        "reality_dest": inbound.reality_dest,
        "reality_private_key": inbound.reality_private_key or None,
        "reality_short_ids": inbound.reality_short_ids or [],
        "reality_spider_x": inbound.reality_spider_x,
        "cert_file": (inbound.extra or {}).get("cert_file"),
        "key_file": (inbound.extra or {}).get("key_file"),
    }


def sync_node(db: Session, node: Node, *, actor_id: Optional[int] = None) -> dict[str, Any]:
    """Push inbound definitions, then the users of every active inbound."""
    if settings.live_proxy_enabled:
        applied = 0
        for inbound in node.inbounds:
            if not inbound.is_active or not uses_live_proxy(inbound):
                continue
            ok, detail = sync_inbound(db, inbound, actor_id=actor_id)
            if ok:
                applied += 1
            else:
                return {"node_id": node.id, "node": node.name, "ok": False, "applied": applied, "errors": [detail]}
        return {"node_id": node.id, "node": node.name, "ok": True, "applied": applied, "errors": []}
    client = NodeClient(node)
    specs = [inbound_spec(i) for i in node.inbounds if i.is_active]

    inbound_resp = client.apply_inbounds(specs)
    if not inbound_resp.ok:
        _record_node_failure(db, node, inbound_resp.error)
        db.commit()
        return {"node_id": node.id, "node": node.name, "ok": False, "applied": 0, "errors": [inbound_resp.error or "inbound apply failed"]}
    _record_node_success(db, node, inbound_resp)

    applied = 0
    errors: list[str] = []
    for inbound in node.inbounds:
        if not inbound.is_active:
            continue
        ok, detail = sync_inbound(db, inbound, actor_id=actor_id)
        if ok:
            applied += 1
        else:
            errors.append(f"{inbound.tag}: {detail}")
    return {"node_id": node.id, "node": node.name, "ok": not errors, "applied": applied, "errors": errors}


def _record_node_success(db: Session, node: Node, resp) -> None:
    node.consecutive_failures = 0
    node.last_error = None
    node.last_heartbeat_at = utcnow()
    if node.status in (NodeStatus.offline, NodeStatus.unknown):
        node.status = NodeStatus.online
    db.add(node)


def _record_node_failure(db: Session, node: Node, error: Optional[str]) -> None:
    node.consecutive_failures = (node.consecutive_failures or 0) + 1
    node.last_error = (error or "unknown error")[:500]
    if node.consecutive_failures >= 3:
        node.status = NodeStatus.offline
    elif node.consecutive_failures >= 1:
        node.status = NodeStatus.degraded
    db.add(node)


# --------------------------------------------------------------------------- #
#  Traffic accounting
# --------------------------------------------------------------------------- #
def apply_usage(db: Session, node: Node, stats: dict[str, dict[str, int]]) -> int:
    """`stats` maps email_tag -> {"up": int, "down": int} cumulative counters."""
    if not stats:
        return 0
    services = list(
        db.execute(select(Service).where(Service.node_id == node.id, Service.email_tag.in_(list(stats))))
        .unique()
        .scalars()
    )
    today = date.today()
    updated = 0
    for service in services:
        counters = stats[service.email_tag]
        raw_up = int(counters.get("up", 0))
        raw_down = int(counters.get("down", 0))

        delta_up = max(raw_up - service.last_raw_up, 0) if raw_up >= service.last_raw_up else raw_up
        delta_down = max(raw_down - service.last_raw_down, 0) if raw_down >= service.last_raw_down else raw_down
        if delta_up == 0 and delta_down == 0:
            continue

        service.last_raw_up = raw_up
        service.last_raw_down = raw_down
        service.used_up_bytes += delta_up
        service.used_down_bytes += delta_down
        service.last_synced_at = utcnow()
        if delta_up or delta_down:
            service.last_connected_at = utcnow()

        row = db.execute(
            select(TrafficDaily).where(TrafficDaily.service_id == service.id, TrafficDaily.day == today)
        ).scalar_one_or_none()
        if row is None:
            row = TrafficDaily(
                service_id=service.id,
                user_id=service.user_id,
                node_id=service.node_id,
                day=today,
                up_bytes=0,
                down_bytes=0,
            )
            db.add(row)
        row.up_bytes += delta_up
        row.down_bytes += delta_down
        updated += 1

    db.commit()
    return updated


def enforce_quota(db: Session, *, actor_id: Optional[int] = None) -> list[dict[str, Any]]:
    """Disable services that exhausted their quota. Returns the actions taken."""
    if not settings.quota_enforcement_enabled:
        return []
    actions: list[dict[str, Any]] = []
    candidates = list(
        db.execute(
            select(Service).where(
                Service.status == ServiceStatus.active, Service.traffic_limit_bytes > 0
            )
        )
        .unique()
        .scalars()
    )
    for service in candidates:
        if not service.is_quota_exhausted:
            continue
        service.status = ServiceStatus.limited
        ok, error = remove_service(db, service)
        service.is_synced = ok
        service.sync_error = error
        actions.append(
            {
                "service_id": service.id,
                "user_id": service.user_id,
                "reason": "quota_exhausted",
                "used_bytes": service.used_bytes,
                "limit_bytes": service.traffic_limit_bytes,
            }
        )
        audit(
            db,
            action="service.limited",
            actor_type="system",
            actor_id=actor_id,
            entity_type="service",
            entity_id=service.id,
            meta={"used": service.used_bytes, "limit": service.traffic_limit_bytes},
        )
    if actions:
        db.commit()
    return actions


def expire_services(db: Session, *, actor_id: Optional[int] = None) -> list[dict[str, Any]]:
    """Move past-due services to `expired` and cut them off on the node."""
    now = utcnow()
    grace = timedelta(hours=settings.grace_period_hours)
    actions: list[dict[str, Any]] = []
    candidates = list(
        db.execute(
            select(Service).where(Service.status == ServiceStatus.active, Service.expires_at.isnot(None))
        )
        .unique()
        .scalars()
    )
    for service in candidates:
        expiry = as_utc(service.expires_at)
        if expiry is None or expiry > now:
            continue
        if not settings.auto_disable_on_expiry and (now - expiry) < grace:
            continue
        service.status = ServiceStatus.expired
        ok, error = remove_service(db, service)
        service.is_synced = ok
        service.sync_error = error
        actions.append({"service_id": service.id, "user_id": service.user_id, "reason": "expired"})
        audit(
            db,
            action="service.expired",
            actor_type="system",
            actor_id=actor_id,
            entity_type="service",
            entity_id=service.id,
            meta={"expires_at": service.expires_at.isoformat()},
        )
    if actions:
        db.commit()
    return actions


def services_expiring_soon(db: Session, days: int = 3) -> list[Service]:
    horizon = utcnow() + timedelta(days=days)
    return list(
        db.execute(
            select(Service).where(
                Service.status == ServiceStatus.active,
                Service.expires_at.isnot(None),
                Service.expires_at <= horizon,
                Service.expires_at > utcnow(),
            )
        )
        .unique()
        .scalars()
    )


def collect_node_stats(db: Session, node: Node, *, reset: bool = False) -> int:
    """Pull counters from a node and fold the deltas into the database."""
    client = NodeClient(node)
    resp = client.stats(reset=reset)
    if not resp.ok:
        _record_node_failure(db, node, resp.error)
        db.commit()
        return 0
    _record_node_success(db, node, resp)
    db.commit()
    return apply_usage(db, node, resp.data.get("users", {}) or {})
