"""Row -> API/bot payload converters (single source of truth for shapes)."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Node, Payment, Service, User
from app.services.xray_links import build_link, build_links


def _note_int(note: Optional[str], key: str) -> int:
    marker = f"{key}="
    for item in (note or "").split():
        if item.startswith(marker):
            try:
                return max(int(item[len(marker) :]), 0)
            except ValueError:
                return 0
    return 0


def service_to_out(service: Service, *, include_links: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": service.id,
        "user_id": service.user_id,
        "username": service.user.username if service.user else None,
        "plan_id": service.plan_id,
        "plan_name": service.plan.name if service.plan else None,
        "node_id": service.node_id,
        "node_name": service.node.name if service.node else None,
        "inbound_id": service.inbound_id,
        "inbound_tag": service.inbound.tag if service.inbound else None,
        "label": service.label,
        "protocol": service.protocol,
        "uuid": service.uuid,
        "email_tag": service.email_tag,
        "status": service.status,
        "started_at": service.started_at,
        "expires_at": service.expires_at,
        "days_left": service.days_left,
        "traffic_limit_bytes": service.traffic_limit_bytes,
        "connection_limit": _note_int(service.note, "connection_limit"),
        "ip_limit": _note_int(service.note, "ip_limit"),
        "speed_limit_mbps": round(_note_int(service.note, "speed_limit_bytes") * 8 / 1_000_000, 2),
        "used_up_bytes": service.used_up_bytes,
        "used_down_bytes": service.used_down_bytes,
        "used_bytes": service.used_bytes,
        "usage_percent": service.usage_percent,
        "last_synced_at": service.last_synced_at,
        "last_connected_at": service.last_connected_at,
        "is_synced": service.is_synced,
        "auto_renew": service.auto_renew,
        "note": service.note,
        "subscription_url": service.subscription_url,
        "created_at": service.created_at,
    }
    if include_links:
        payload["links"] = build_links(service)
        payload["raw_link"] = build_link(service)
        payload["qr_png_url"] = f"/api/v1/subscription/{service.sub_token}/qr.png"
    return payload


def user_to_out(user: User, *, service_count: int = 0, total_used_bytes: int = 0) -> dict[str, Any]:
    return {
        "id": user.id,
        "uuid": user.uuid,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "balance": round(user.balance, 2),
        "telegram_id": user.telegram_id,
        "telegram_username": user.telegram_username,
        "referral_code": user.referral_code,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "note": user.note,
        "service_count": service_count,
        "total_used_bytes": total_used_bytes,
    }


def node_to_out(node: Node, *, service_count: int = 0) -> dict[str, Any]:
    return {
        "id": node.id,
        "name": node.name,
        "address": node.address,
        "public_host": node.public_host,
        "region": node.region,
        "tags": node.tags or [],
        "status": node.status,
        "is_active": node.is_active,
        "weight": node.weight,
        "max_services": node.max_services,
        "xray_version": node.xray_version,
        "last_heartbeat_at": node.last_heartbeat_at,
        "cpu_percent": node.cpu_percent,
        "memory_percent": node.memory_percent,
        "disk_percent": node.disk_percent,
        "uptime_seconds": node.uptime_seconds,
        "online_users": node.online_users,
        "last_error": node.last_error,
        "service_count": service_count,
    }


def payment_to_out(payment: Payment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "reference": payment.reference,
        "user_id": payment.user_id,
        "username": payment.user.username if payment.user else None,
        "plan_id": payment.plan_id,
        "plan_name": payment.plan.name if payment.plan else None,
        "service_id": payment.service_id,
        "purpose": payment.purpose,
        "amount": payment.amount,
        "currency": payment.currency,
        "method": payment.method,
        "status": payment.status,
        "provider_ref": payment.provider_ref,
        "receipt_url": payment.receipt_url,
        "reject_reason": payment.reject_reason,
        "paid_at": payment.paid_at,
        "created_at": payment.created_at,
    }


def count_services_by_user(db: Session, user_ids: Optional[list[int]] = None) -> dict[int, int]:
    stmt = select(Service.user_id, func.count(Service.id)).group_by(Service.user_id)
    if user_ids:
        stmt = stmt.where(Service.user_id.in_(user_ids))
    return {row[0]: row[1] for row in db.execute(stmt).all()}


def usage_by_user(db: Session, user_ids: Optional[list[int]] = None) -> dict[int, int]:
    stmt = select(
        Service.user_id,
        func.coalesce(func.sum(Service.used_up_bytes + Service.used_down_bytes), 0),
    ).group_by(Service.user_id)
    if user_ids:
        stmt = stmt.where(Service.user_id.in_(user_ids))
    return {row[0]: int(row[1] or 0) for row in db.execute(stmt).all()}


def count_services_by_node(db: Session) -> dict[int, int]:
    stmt = select(Service.node_id, func.count(Service.id)).group_by(Service.node_id)
    return {row[0]: row[1] for row in db.execute(stmt).all()}
