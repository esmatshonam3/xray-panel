"""Node + inbound administration, health checks and synchronisation."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, CurrentUser, DbSession, Paging, StaffUser
from app.core.config import settings
from app.core.security import generate_token
from app.db.models import Inbound, Node, NodeStatus, Protocol, Security, Service, Transport
from app.schemas import (
    InboundCheck,
    InboundCreate,
    InboundOut,
    InboundUpdate,
    Message,
    NodeCreate,
    NodeHealth,
    NodeOut,
    NodeSyncResult,
    NodeUpdate,
    Page,
)
from app.services.audit import audit
from app.services.node_client import NodeClient
from app.services.provisioning import sync_inbound, sync_node
from app.services.serializers import count_services_by_node, node_to_out

router = APIRouter(prefix="/nodes", tags=["nodes"])


# --------------------------------------------------------------------------- #
#  Nodes
# --------------------------------------------------------------------------- #
@router.get("", response_model=Page[NodeOut])
def list_nodes(db: DbSession, paging: Paging, _: StaffUser) -> Page[NodeOut]:
    stmt = select(Node)
    if paging.q:
        like = f"%{paging.q}%"
        stmt = stmt.where(Node.name.ilike(like) | Node.public_host.ilike(like))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(db.execute(stmt.order_by(Node.name).limit(paging.size).offset(paging.offset)).scalars())
    counts = count_services_by_node(db)
    return Page.build(
        [NodeOut.model_validate(node_to_out(n, service_count=counts.get(n.id, 0))) for n in rows],
        total,
        paging.page,
        paging.size,
    )


@router.post("", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
def create_node(payload: NodeCreate, db: DbSession, actor: AdminUser) -> NodeOut:
    if db.execute(select(Node).where(Node.name == payload.name)).scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Node name already exists")
    node = Node(
        name=payload.name,
        address=payload.address,
        public_host=payload.public_host,
        region=payload.region,
        tags=payload.tags,
        is_active=payload.is_active,
        weight=payload.weight,
        max_services=payload.max_services,
        status=NodeStatus.unknown,
    )
    node.api_token = payload.api_token
    db.add(node)
    db.flush()
    audit(db, action="node.create", actor_id=actor.id, entity_type="node", entity_id=node.id, meta={"name": node.name})
    db.commit()

    health = NodeClient(node).health()
    return NodeOut.model_validate(node_to_out(node, service_count=0)) if not health.ok else NodeOut.model_validate(
        node_to_out(_refresh_from_health(db, node, health), service_count=0)
    )


@router.post("/railway", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
def create_railway_node(db: DbSession, actor: AdminUser) -> NodeOut:
    """Register a node-agent running as a private service in this Railway project."""
    if not settings.railway_tcp_proxy_domain or not settings.railway_tcp_proxy_port or not settings.railway_node_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Railway Node Agent is not configured. Set RAILWAY_TCP_PROXY_DOMAIN, RAILWAY_TCP_PROXY_PORT, and RAILWAY_NODE_TOKEN on the panel service.",
        )
    name = "railway-xray"
    node = db.execute(select(Node).where(Node.name == name)).scalar_one_or_none()
    created = node is None
    if node is None:
        node = Node(
            name=name,
            address="http://node-agent.railway.internal:8081",
            public_host=settings.railway_tcp_proxy_domain,
            region="Railway",
            tags=["railway", "default"],
            is_active=True,
            max_services=0,
            status=NodeStatus.unknown,
        )
        db.add(node)
    else:
        # Re-registering is also the recovery path for a node token that can
        # no longer be decrypted after an encryption-key change or bad seed.
        node.address = "http://node-agent.railway.internal:8081"
        node.public_host = settings.railway_tcp_proxy_domain
        node.region = "Railway"
        node.tags = ["railway", "default"]
        node.is_active = True
    node.api_token = settings.railway_node_token
    db.flush()
    audit(
        db,
        action="node.create" if created else "node.update",
        actor_id=actor.id,
        entity_type="node",
        entity_id=node.id,
        meta={"name": node.name, "provider": "railway", "token_refreshed": not created},
    )
    db.commit()
    health = NodeClient(node).health()
    if health.ok:
        node = _refresh_from_health(db, node, health)
    return NodeOut.model_validate(node_to_out(node, service_count=0))


@router.get("/{node_id}", response_model=NodeOut)
def get_node(node_id: int, db: DbSession, _: StaffUser) -> NodeOut:
    node = _load_node(db, node_id)
    counts = count_services_by_node(db)
    return NodeOut.model_validate(node_to_out(node, service_count=counts.get(node.id, 0)))


@router.patch("/{node_id}", response_model=NodeOut)
def update_node(node_id: int, payload: NodeUpdate, db: DbSession, actor: AdminUser) -> NodeOut:
    node = _load_node(db, node_id)
    data = payload.model_dump(exclude_unset=True)
    token = data.pop("api_token", None)
    if token:
        node.api_token = token
    for key, value in data.items():
        if value is not None:
            setattr(node, key, value)
    audit(db, action="node.update", actor_id=actor.id, entity_type="node", entity_id=node.id, meta={"fields": list(data) + (["api_token"] if token else [])})
    db.commit()
    counts = count_services_by_node(db)
    return NodeOut.model_validate(node_to_out(node, service_count=counts.get(node.id, 0)))


@router.post("/{node_id}/rotate-token", response_model=dict)
def rotate_token(node_id: int, db: DbSession, actor: AdminUser) -> dict:
    node = _load_node(db, node_id)
    raw = generate_token(32, prefix="nd_")
    node.api_token = raw
    audit(db, action="node.token_rotate", actor_id=actor.id, entity_type="node", entity_id=node.id, meta={"name": node.name})
    db.commit()
    return {"api_token": raw, "hint": "Update NODE_TOKEN on the agent and restart it."}


@router.get("/{node_id}/health", response_model=NodeHealth)
def node_health(node_id: int, db: DbSession, _: StaffUser) -> NodeHealth:
    node = _load_node(db, node_id)
    resp = NodeClient(node).health()
    if not resp.ok:
        node.status = NodeStatus.offline
        node.consecutive_failures = (node.consecutive_failures or 0) + 1
        node.last_error = (resp.error or "unreachable")[:500]
        db.commit()
        return NodeHealth(
            node_id=node.id,
            name=node.name,
            status=node.status,
            reachable=False,
            error=resp.error,
            checked_at=__import__("app.db.base", fromlist=["utcnow"]).utcnow(),
        )
    node = _refresh_from_health(db, node, resp)
    data = resp.data
    return NodeHealth(
        node_id=node.id,
        name=node.name,
        status=node.status,
        reachable=True,
        latency_ms=resp.latency_ms,
        xray_running=data.get("xray_running"),
        xray_version=data.get("xray_version"),
        cpu_percent=data.get("cpu_percent"),
        memory_percent=data.get("memory_percent"),
        disk_percent=data.get("disk_percent"),
        uptime_seconds=data.get("uptime_seconds"),
        active_users=data.get("active_users"),
        checked_at=node.last_heartbeat_at,
    )


@router.post("/{node_id}/sync", response_model=NodeSyncResult)
def sync(node_id: int, db: DbSession, actor: AdminUser) -> NodeSyncResult:
    node = _load_node(db, node_id)
    result = sync_node(db, node, actor_id=actor.id)
    return NodeSyncResult(
        node_id=node.id,
        node_name=node.name,
        ok=result["ok"],
        applied=result["applied"],
        error="; ".join(result["errors"]) or None,
    )


@router.post("/{node_id}/restart-xray", response_model=Message)
def restart_xray(node_id: int, db: DbSession, actor: AdminUser) -> Message:
    node = _load_node(db, node_id)
    resp = NodeClient(node).restart()
    audit(db, action="node.update", actor_id=actor.id, entity_type="node", entity_id=node.id, meta={"restart_xray": True, "ok": resp.ok}, status="success" if resp.ok else "failure")
    db.commit()
    if not resp.ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, resp.error or "restart failed")
    return Message(detail=f"Xray restarted on {node.name}")


@router.delete("/{node_id}", response_model=Message)
def delete_node(node_id: int, db: DbSession, actor: AdminUser) -> Message:
    node = _load_node(db, node_id)
    active = db.execute(
        select(func.count(Service.id)).where(Service.node_id == node.id)
    ).scalar_one()
    if active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Node still hosts {active} service(s); migrate or delete them first.",
        )
    audit(db, action="node.delete", actor_id=actor.id, entity_type="node", entity_id=node.id, meta={"name": node.name})
    db.delete(node)
    db.commit()
    return Message(detail="Node deleted")


def _load_node(db, node_id: int) -> Node:
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
    return node


def _refresh_from_health(db, node: Node, resp) -> Node:
    from app.db.base import utcnow

    data = resp.data or {}
    node.status = NodeStatus.online
    node.consecutive_failures = 0
    node.last_error = None
    node.last_heartbeat_at = utcnow()
    node.xray_version = data.get("xray_version") or node.xray_version
    node.cpu_percent = data.get("cpu_percent")
    node.memory_percent = data.get("memory_percent")
    node.disk_percent = data.get("disk_percent")
    node.uptime_seconds = data.get("uptime_seconds")
    node.online_users = data.get("active_users")
    db.commit()
    return node


# --------------------------------------------------------------------------- #
#  Inbounds
# --------------------------------------------------------------------------- #
inbound_router = APIRouter(prefix="/inbounds", tags=["inbounds"])


@inbound_router.get("", response_model=list[InboundOut])
def list_inbounds(db: DbSession, _: StaffUser, node_id: Optional[int] = None) -> list[InboundOut]:
    stmt = select(Inbound)
    if node_id:
        stmt = stmt.where(Inbound.node_id == node_id)
    rows = list(db.execute(stmt.order_by(Inbound.sort_order, Inbound.id)).unique().scalars())
    counts = {
        row[0]: row[1]
        for row in db.execute(select(Service.inbound_id, func.count(Service.id)).group_by(Service.inbound_id)).all()
    }
    out: list[InboundOut] = []
    for inbound in rows:
        payload = {c.name: getattr(inbound, c.name) for c in Inbound.__table__.columns if c.name in InboundOut.model_fields}
        payload["service_count"] = counts.get(inbound.id, 0)
        out.append(InboundOut.model_validate(payload))
    return out


@inbound_router.post("", response_model=InboundOut, status_code=status.HTTP_201_CREATED)
def create_inbound(payload: InboundCreate, db: DbSession, actor: AdminUser) -> InboundOut:
    if db.get(Node, payload.node_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Node not found")
    exists = db.execute(
        select(Inbound).where(Inbound.node_id == payload.node_id, Inbound.tag == payload.tag)
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Inbound tag already used on this node")

    data = payload.model_dump(exclude={"reality_private_key", "ss_password"})
    node = db.get(Node, payload.node_id)
    if node.name == "railway-xray":
        # Railway's TCP proxy listens on an externally allocated port and
        # forwards to the container's target port (the Xray inbound port).
        # Do not replace the internal listen port with the proxy port.
        data["public_port"] = payload.public_port or settings.railway_tcp_proxy_port or payload.port
        data["port"] = payload.port
        data["public_host"] = payload.public_host or settings.railway_tcp_proxy_domain or node.public_host
    inbound = Inbound(**data)
    if payload.reality_private_key:
        inbound.reality_private_key = payload.reality_private_key
    if payload.protocol == Protocol.shadowsocks and not payload.ss_method:
        inbound.ss_method = "chacha20-ietf-poly1305"
    db.add(inbound)
    db.flush()
    audit(db, action="inbound.create", actor_id=actor.id, entity_type="inbound", entity_id=inbound.id, meta={"tag": inbound.tag, "protocol": inbound.protocol.value})
    db.commit()
    return InboundOut.model_validate({**{c: getattr(inbound, c) for c in InboundOut.model_fields if hasattr(inbound, c)}, "service_count": 0})


@inbound_router.patch("/{inbound_id}", response_model=InboundOut)
def update_inbound(inbound_id: int, payload: InboundUpdate, db: DbSession, actor: AdminUser) -> InboundOut:
    inbound = db.get(Inbound, inbound_id)
    if inbound is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    data = payload.model_dump(exclude_unset=True)
    private_key = data.pop("reality_private_key", None)
    for key, value in data.items():
        if value is not None:
            setattr(inbound, key, value)
    if private_key:
        inbound.reality_private_key = private_key
    audit(db, action="inbound.update", actor_id=actor.id, entity_type="inbound", entity_id=inbound.id, meta={"fields": list(data)})
    db.commit()
    ok, detail = sync_inbound(db, inbound, actor_id=actor.id)
    return InboundOut.model_validate(
        {**{c: getattr(inbound, c) for c in InboundOut.model_fields if hasattr(inbound, c)}, "service_count": len(inbound.services)}
    )


@inbound_router.post("/{inbound_id}/sync", response_model=Message)
def sync_single_inbound(inbound_id: int, db: DbSession, actor: AdminUser) -> Message:
    inbound = db.get(Inbound, inbound_id)
    if inbound is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    ok, detail = sync_inbound(db, inbound, actor_id=actor.id)
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail)
    return Message(detail=detail)


@inbound_router.get("/{inbound_id}/validate", response_model=InboundCheck)
def validate_inbound(inbound_id: int, db: DbSession, _: StaffUser) -> InboundCheck:
    inbound = db.get(Inbound, inbound_id)
    if inbound is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    errors: list[str] = []
    warnings: list[str] = []

    if inbound.security == Security.reality:
        if not inbound.reality_public_key or not inbound.reality_private_key:
            errors.append("Reality requires both public and private keys")
        if not inbound.reality_short_ids:
            errors.append("Reality requires at least one short id")
        if inbound.protocol not in (Protocol.vless,):
            errors.append("Reality is only valid for VLESS")
    if inbound.security == Security.tls and not (inbound.sni or inbound.public_host):
        warnings.append("TLS without SNI - clients may fail certificate validation")
    if inbound.transport in (Transport.ws, Transport.httpupgrade, Transport.xhttp) and not inbound.path:
        warnings.append("Path is empty; clients will use '/'")
    if inbound.transport == Transport.grpc and not inbound.service_name:
        warnings.append("gRPC serviceName is empty; clients will use 'grpc'")
    if inbound.protocol == Protocol.shadowsocks and not inbound.ss_method:
        errors.append("Shadowsocks requires a cipher method")
    if not inbound.node.is_active:
        warnings.append("The parent node is disabled")

    conflict = db.execute(
        select(Inbound).where(
            Inbound.node_id == inbound.node_id, Inbound.port == inbound.port, Inbound.id != inbound.id
        )
    ).scalar_one_or_none()
    if conflict:
        errors.append(f"Port {inbound.port} is already used by inbound '{conflict.tag}'")

    return InboundCheck(ok=not errors, errors=errors, warnings=warnings)


@inbound_router.delete("/{inbound_id}", response_model=Message)
def delete_inbound(inbound_id: int, db: DbSession, actor: AdminUser) -> Message:
    inbound = db.get(Inbound, inbound_id)
    if inbound is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    count = db.execute(select(func.count(Service.id)).where(Service.inbound_id == inbound.id)).scalar_one()
    if count:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Inbound hosts {count} service(s); remove them first.")
    audit(db, action="inbound.delete", actor_id=actor.id, entity_type="inbound", entity_id=inbound.id, meta={"tag": inbound.tag})
    db.delete(inbound)
    db.commit()
    return Message(detail="Inbound deleted")


@inbound_router.get("/{inbound_id}/clients")
def inbound_clients(inbound_id: int, db: DbSession, _: StaffUser) -> list[dict]:
    """Live client list straight from the node (Xray API), for debugging."""
    inbound = db.get(Inbound, inbound_id)
    if inbound is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    resp = NodeClient(inbound.node).inbound_config(inbound.tag)
    if not resp.ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, resp.error or "node error")
    return resp.data.get("clients", [])
