"""Immutable audit trail helpers."""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AuditLog

log = get_logger(__name__)

# Actions that must always be recorded even if the caller forgot to pass a meta.
SENSITIVE_ACTIONS = {
    "auth.login",
    "auth.login_failed",
    "auth.logout",
    "user.create",
    "user.update",
    "user.delete",
    "user.role_change",
    "user.password_reset",
    "node.create",
    "node.update",
    "node.delete",
    "node.token_rotate",
    "inbound.create",
    "inbound.update",
    "inbound.delete",
    "payment.approve",
    "payment.reject",
    "settings.update",
    "backup.create",
    "backup.restore",
}


def audit(
    db: Session,
    *,
    action: str,
    actor_id: Optional[int] = None,
    actor_type: str = "user",
    actor_label: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    status: str = "success",
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
    commit: bool = False,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        actor_id=actor_id,
        actor_type=actor_type,
        actor_label=actor_label,
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:255] or None,
        meta=meta or {},
    )
    db.add(entry)
    if commit:
        db.commit()
    if action in SENSITIVE_ACTIONS:
        log.info(
            "audit",
            extra={
                "action": action,
                "actor_id": actor_id,
                "actor_type": actor_type,
                "entity": f"{entity_type}:{entity_id}",
                "status": status,
            },
        )
    return entry


def recent_audit(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    action: Optional[str] = None,
    actor_id: Optional[int] = None,
    entity_type: Optional[str] = None,
) -> tuple[list[AuditLog], int]:
    from sqlalchemy import func

    stmt = select(AuditLog)
    count_stmt = select(func.count(AuditLog.id))
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
        count_stmt = count_stmt.where(AuditLog.action.like(f"{action}%"))
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
        count_stmt = count_stmt.where(AuditLog.actor_id == actor_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
        count_stmt = count_stmt.where(AuditLog.entity_type == entity_type)

    total = db.execute(count_stmt).scalar_one()
    rows = list(
        db.execute(stmt.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)).scalars()
    )
    return rows, total
