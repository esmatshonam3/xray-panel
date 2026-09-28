"""Runtime settings, audit log browsing and backup management."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import AdminUser, DbSession, Paging, StaffUser
from app.core.config import settings as env_settings
from app.db.models import Setting, User
from app.schemas import AuditLogOut, Message, Page, SettingOut, SettingUpdate
from app.services.audit import audit, recent_audit
from app.services.backup import create_backup, disk_usage, list_backups, prune_backups, restore_backup

router = APIRouter(tags=["settings"])

# Keys the panel is allowed to override at runtime (env vars stay the fallback).
EDITABLE_KEYS = {
    "branding.name",
    "branding.support_url",
    "branding.telegram_channel",
    "payments.instructions",
    "payments.card_number",
    "payments.card_holder",
    "payments.crypto_address",
    "payments.stars_enabled",
    "plans.default_currency",
    "notifications.expiry_reminder_days",
    "notifications.low_quota_percent",
    "security.registration_open",
    "security.max_services_per_user",
}


def get_setting(db, key: str, default: Any = None) -> Any:
    row = db.get(Setting, key)
    return row.value if row is not None else default


@router.get("/settings", response_model=list[SettingOut])
def list_settings(db: DbSession, _: StaffUser) -> list[SettingOut]:
    rows = list(db.execute(select(Setting).order_by(Setting.key)).scalars())
    return [SettingOut.model_validate(r) for r in rows]


@router.put("/settings/{key}", response_model=SettingOut)
def upsert_setting(key: str, payload: SettingUpdate, db: DbSession, actor: AdminUser) -> SettingOut:
    if key not in EDITABLE_KEYS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{key}' is not editable at runtime. Allowed: {sorted(EDITABLE_KEYS)}",
        )
    row = db.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=payload.value, description=payload.description)
        db.add(row)
    else:
        row.value = payload.value
        if payload.description:
            row.description = payload.description
    row.updated_by_id = actor.id
    audit(db, action="settings.update", actor_id=actor.id, entity_type="setting", meta={"key": key, "value": payload.value})
    db.commit()
    return SettingOut.model_validate(row)


@router.get("/settings/public")
def public_settings(db: DbSession) -> dict:
    """Branding + payment info the login page and bot need before auth."""
    keys = [
        "branding.name",
        "branding.support_url",
        "branding.telegram_channel",
        "payments.instructions",
        "payments.card_number",
        "payments.card_holder",
        "payments.crypto_address",
        "security.registration_open",
    ]
    rows = {r.key: r.value for r in db.execute(select(Setting).where(Setting.key.in_(keys))).scalars()}
    return {
        "app_name": rows.get("branding.name", env_settings.app_name),
        "support_url": rows.get("branding.support_url"),
        "telegram_channel": rows.get("branding.telegram_channel"),
        "payment_instructions": rows.get("payments.instructions", env_settings.telegram_payment_instructions),
        "card_number": rows.get("payments.card_number"),
        "card_holder": rows.get("payments.card_holder"),
        "crypto_address": rows.get("payments.crypto_address"),
        "registration_open": rows.get("security.registration_open", True),
        "telegram_bot_username": env_settings.telegram_bot_username,
    }


# --------------------------------------------------------------------------- #
#  Audit log
# --------------------------------------------------------------------------- #
@router.get("/logs/audit", response_model=Page[AuditLogOut])
def audit_logs(
    db: DbSession,
    paging: Paging,
    _: StaffUser,
    action: Optional[str] = None,
    actor_id: Optional[int] = None,
    entity_type: Optional[str] = None,
) -> Page[AuditLogOut]:
    rows, total = recent_audit(
        db, limit=paging.size, offset=paging.offset, action=action, actor_id=actor_id, entity_type=entity_type
    )
    return Page.build([AuditLogOut.model_validate(r) for r in rows], total, paging.page, paging.size)


# --------------------------------------------------------------------------- #
#  Backups
# --------------------------------------------------------------------------- #
@router.get("/backups")
def backups(db: DbSession, _: StaffUser) -> dict:
    records = list_backups(db)
    return {
        "items": [
            {
                "id": r.id,
                "filename": r.filename,
                "size_bytes": r.size_bytes,
                "checksum": r.checksum,
                "status": r.status,
                "error": r.error,
                "created_at": r.created_at.isoformat(),
            }
            for r in records
        ],
        "disk": disk_usage(),
        "schedule_hours": env_settings.backup_interval_hours,
        "retention_days": env_settings.backup_retention_days,
    }


@router.post("/backups", response_model=Message)
def run_backup(db: DbSession, actor: AdminUser, label: str = "manual") -> Message:
    record = create_backup(db, label=label)
    audit(db, action="backup.create", actor_id=actor.id, entity_type="backup", entity_id=record.id, meta={"filename": record.filename, "status": record.status})
    db.commit()
    if record.status != "ok":
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, record.error or "backup failed")
    return Message(detail=f"Backup created: {record.filename} ({record.size_bytes} bytes)")


@router.post("/backups/{filename}/restore", response_model=Message)
def restore(db: DbSession, filename: str, actor: AdminUser) -> Message:
    if not env_settings.is_sqlite and env_settings.is_production:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "In-place restore on managed Postgres is disabled; use Render's own PITR or a maintenance window.",
        )
    ok, error = restore_backup(db, filename)
    audit(db, action="backup.restore", actor_id=actor.id, status="success" if ok else "failure", meta={"filename": filename, "error": error})
    db.commit()
    if not ok:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, error or "restore failed")
    return Message(detail="Restore completed - restart the service to clear caches")


@router.post("/backups/prune", response_model=Message)
def prune(db: DbSession, actor: AdminUser, days: Optional[int] = None) -> Message:
    removed = prune_backups(db, retention_days=days)
    audit(db, action="backup.prune", actor_id=actor.id, meta={"removed": removed})
    db.commit()
    return Message(detail=f"{removed} old backup(s) removed")


# --------------------------------------------------------------------------- #
#  System info
# --------------------------------------------------------------------------- #
@router.get("/system/info")
def system_info(db: DbSession, _: AdminUser) -> dict:
    from app.services import runtime_config

    telegram = runtime_config.telegram_config(db, fresh=True)
    return {
        "settings": env_settings.public_dict(),
        "counts": {"users": db.query(User).count()},
        "database": {
            "driver": env_settings.sqlalchemy_url.split("://")[0],
            "sqlite": env_settings.is_sqlite,
        },
        "paths": {"backup_dir": env_settings.backup_dir},
        "integrations": {
            "telegram_configured": bool(telegram.get("token")),
            "telegram_enabled": bool(telegram.get("enabled")) and bool(telegram.get("token")),
            "telegram_source": runtime_config.source_of(runtime_config.TELEGRAM_TOKEN, db),
            "telegram_webhook": runtime_config.webhook_url(db) if telegram.get("token") else None,
            "alert_webhook": bool(env_settings.alert_webhook_url),
        },
    }
