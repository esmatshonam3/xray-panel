"""Service (Xray config) management: create, renew, bulk ops, sync, links."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, or_, select

from app.api.deps import AdminUser, CurrentUser, DbSession, Paging, StaffUser
from app.db.models import Inbound, Plan, Service, ServiceStatus, User
from app.schemas import (
    Message,
    Page,
    ServiceBulkAction,
    ServiceCreate,
    ServiceDetail,
    ServiceOut,
    ServiceRenew,
    ServiceUpdate,
)
from app.services.audit import audit
from app.services.provisioning import (
    ProvisioningError,
    collect_node_stats,
    create_service,
    delete_service,
    push_service,
    renew_service,
    set_status,
    selectable_nodes,
    sync_inbound,
    eligible_inbounds,
    uses_live_proxy,
)
from app.services.serializers import service_to_out
from app.services.xray_links import build_link, qr_png

router = APIRouter(prefix="/services", tags=["services"])


@router.get("/available-nodes")
def list_config_nodes(db: DbSession, _: StaffUser, plan_id: Optional[int] = None) -> list[dict]:
    """List selectable connection locations for the config-creation dialog."""
    plan = db.get(Plan, plan_id) if plan_id else None
    if plan_id and plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    return [
        {"id": node.id, "name": node.name, "region": node.region, "status": node.status.value, "tags": node.tags or []}
        for node in selectable_nodes(db, plan)
    ]


@router.get("/available-inbounds")
def list_config_inbounds(
    db: DbSession,
    _: StaffUser,
    plan_id: Optional[int] = None,
    node_id: Optional[int] = None,
) -> list[dict]:
    """List protocol/transport endpoints filtered by the chosen location."""
    plan = db.get(Plan, plan_id) if plan_id else None
    if plan_id and plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    inbounds = eligible_inbounds(db, plan, node_id=node_id)
    return [
        {
            "id": inbound.id,
            "node_id": inbound.node_id,
            "node_name": inbound.node.name,
            "region": inbound.node.region,
            "tag": inbound.tag,
            "remark": inbound.remark,
            "protocol": inbound.protocol.value,
            "transport": inbound.transport.value,
            "public_host": inbound.display_host,
            "public_port": inbound.display_port,
            "path": inbound.path,
        }
        for inbound in inbounds
    ]


@router.get("/connection-locations")
def list_connection_locations(db: DbSession, _: CurrentUser) -> list[dict]:
    """Expose configured locations without revealing node API addresses or tokens."""
    nodes = selectable_nodes(db)
    return [
        {"id": node.id, "name": node.name, "region": node.region, "status": node.status.value}
        for node in nodes
    ]


def _visible_scope(user: User):
    """Non-staff users only ever see their own services."""
    return [] if user.is_staff else [Service.user_id == user.id]


@router.get("", response_model=Page[ServiceOut])
def list_services(
    db: DbSession,
    paging: Paging,
    user: CurrentUser,
    status_filter: Optional[ServiceStatus] = None,
    node_id: Optional[int] = None,
    user_id: Optional[int] = None,
    include_deleted: bool = False,
) -> Page[ServiceOut]:
    stmt = select(Service).where(*_visible_scope(user))
    if not include_deleted:
        stmt = stmt.where(Service.status != ServiceStatus.deleted)
    if status_filter:
        stmt = stmt.where(Service.status == status_filter)
    if node_id:
        stmt = stmt.where(Service.node_id == node_id)
    if user_id and user.is_staff:
        stmt = stmt.where(Service.user_id == user_id)
    if paging.q:
        like = f"%{paging.q}%"
        stmt = stmt.where(
            or_(Service.label.ilike(like), Service.email_tag.ilike(like), Service.sub_token.ilike(like))
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(stmt.order_by(Service.created_at.desc()).limit(paging.size).offset(paging.offset)).unique().scalars()
    )
    return Page.build([ServiceOut.model_validate(service_to_out(s)) for s in rows], total, paging.page, paging.size)


@router.post("", response_model=ServiceDetail, status_code=status.HTTP_201_CREATED)
def create(payload: ServiceCreate, db: DbSession, actor: AdminUser) -> ServiceDetail:
    target = db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target user not found")
    plan = db.get(Plan, payload.plan_id) if payload.plan_id else None
    inbound = db.get(Inbound, payload.inbound_id) if payload.inbound_id else None
    if payload.plan_id and plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found")
    if payload.inbound_id and inbound is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inbound not found")
    if payload.node_id and not eligible_inbounds(db, plan, node_id=payload.node_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "No endpoint on the selected location matches this plan")
    if inbound:
        if inbound not in eligible_inbounds(db, plan, node_id=payload.node_id, inbound_id=inbound.id):
            raise HTTPException(status.HTTP_409_CONFLICT, "Selected endpoint is unavailable or does not match this plan")
    if payload.node_id and inbound and inbound.node_id != payload.node_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Selected endpoint does not belong to the selected location")

    try:
        service = create_service(
            db,
            user=target,
            plan=plan,
            inbound=inbound,
            node_id=payload.node_id,
            duration_days=payload.duration_days,
            traffic_gb=payload.traffic_gb,
            connection_limit=payload.connection_limit,
            ip_limit=payload.ip_limit,
            speed_limit_mbps=payload.speed_limit_mbps,
            expires_at=payload.expires_at,
            label=payload.label,
            note=payload.note,
            auto_renew=payload.auto_renew,
            actor_id=actor.id,
        )
    except ProvisioningError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return ServiceDetail.model_validate(service_to_out(service, include_links=True))


@router.get("/{service_id}", response_model=ServiceDetail)
def get_service(service_id: int, db: DbSession, user: CurrentUser) -> ServiceDetail:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    if not user.is_staff and service.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your service")
    return ServiceDetail.model_validate(service_to_out(service, include_links=True))


@router.patch("/{service_id}", response_model=ServiceDetail)
def update(service_id: int, payload: ServiceUpdate, db: DbSession, actor: AdminUser) -> ServiceDetail:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")

    changes = payload.model_dump(exclude_unset=True)
    if "traffic_gb" in changes and changes["traffic_gb"] is not None:
        service.traffic_limit_bytes = int(changes.pop("traffic_gb") * 1024 ** 3)
    if "status" in changes and changes["status"] is not None:
        new_status = changes.pop("status")
        if new_status != service.status:
            set_status(db, service, new_status, actor_id=actor.id, commit=False)
    for key, value in changes.items():
        if value is not None:
            setattr(service, key, value)

    if service.status == ServiceStatus.active:
        ok, error = push_service(db, service)
        service.is_synced = ok
        service.sync_error = error

    audit(db, action="service.update", actor_id=actor.id, entity_type="service", entity_id=service.id, meta={"changes": list(payload.model_dump(exclude_unset=True))})
    db.commit()
    return ServiceDetail.model_validate(service_to_out(service, include_links=True))


@router.post("/{service_id}/renew", response_model=ServiceDetail)
def renew(service_id: int, payload: ServiceRenew, db: DbSession, actor: AdminUser) -> ServiceDetail:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    renew_service(
        db,
        service,
        days=payload.days,
        reset_traffic=payload.reset_traffic,
        add_traffic_gb=payload.add_traffic_gb,
        actor_id=actor.id,
    )
    return ServiceDetail.model_validate(service_to_out(service, include_links=True))


@router.post("/{service_id}/reset-traffic", response_model=ServiceDetail)
def reset_traffic(service_id: int, db: DbSession, actor: AdminUser) -> ServiceDetail:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    service.used_up_bytes = 0
    service.used_down_bytes = 0
    service.last_raw_up = 0
    service.last_raw_down = 0
    if service.status == ServiceStatus.limited:
        service.status = ServiceStatus.active
    push_service(db, service)
    audit(db, action="service.update", actor_id=actor.id, entity_type="service", entity_id=service.id, meta={"reset_traffic": True})
    db.commit()
    return ServiceDetail.model_validate(service_to_out(service, include_links=True))


@router.post("/{service_id}/sync", response_model=Message)
def sync_one(service_id: int, db: DbSession, actor: AdminUser) -> Message:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    ok, error = push_service(db, service) if service.is_usable else (True, None)
    service.is_synced = ok
    service.sync_error = error
    db.commit()
    if not ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, error or "sync failed")
    return Message(detail="Service synced to node")


@router.post("/{service_id}/pull-usage", response_model=Message)
def pull_usage(service_id: int, db: DbSession, actor: AdminUser) -> Message:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    if uses_live_proxy(service.inbound):
        return Message(detail="Usage is updated live by the built-in WebSocket relay")
    updated = collect_node_stats(db, service.node, reset=False)
    return Message(detail=f"Usage refreshed ({updated} services updated on node {service.node.name})")


@router.delete("/{service_id}", response_model=Message)
def remove(service_id: int, db: DbSession, actor: AdminUser) -> Message:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    delete_service(db, service, actor_id=actor.id)
    return Message(detail="Service removed from node and database")


@router.post("/bulk", response_model=Message)
def bulk(payload: ServiceBulkAction, db: DbSession, actor: AdminUser) -> Message:
    rows = list(db.execute(select(Service).where(Service.id.in_(payload.service_ids))).unique().scalars())
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No matching services")

    affected = 0
    for service in rows:
        if payload.action == "enable":
            set_status(db, service, ServiceStatus.active, actor_id=actor.id, commit=False)
        elif payload.action == "disable":
            set_status(db, service, ServiceStatus.disabled, actor_id=actor.id, commit=False)
        elif payload.action == "delete":
            delete_service(db, service, actor_id=actor.id, commit=False)
        elif payload.action == "reset_traffic":
            service.used_up_bytes = 0
            service.used_down_bytes = 0
            service.last_raw_up = 0
            service.last_raw_down = 0
            if service.status == ServiceStatus.limited:
                service.status = ServiceStatus.active
                ok, error = push_service(db, service)
                service.is_synced = ok
                service.sync_error = error
        elif payload.action == "sync":
            ok, error = push_service(db, service) if service.is_usable else (True, None)
            service.is_synced = ok
            service.sync_error = error
        affected += 1

    db.commit()
    return Message(detail=f"{affected} service(s) processed: {payload.action}")


@router.get("/{service_id}/link", response_class=Response)
def raw_link(service_id: int, db: DbSession, user: CurrentUser) -> Response:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    if not user.is_staff and service.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your service")
    return Response(content=build_link(service), media_type="text/plain")


@router.get("/{service_id}/qr.png")
def service_qr(service_id: int, db: DbSession, user: CurrentUser) -> Response:
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Service not found")
    if not user.is_staff and service.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your service")
    return Response(
        content=qr_png(build_link(service)),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )
