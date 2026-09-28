"""Telegram webhook ingress + bot configuration managed from the panel.

Everything an operator needs to bring the bot online lives here:

    GET    /telegram/config            read effective config (secrets masked)
    PUT    /telegram/config            save token / username / admin IDs / toggles
    DELETE /telegram/config            drop DB overrides, fall back to the environment
    POST   /telegram/generate-secret   random webhook secret
    POST   /telegram/validate          verify a token with getMe (before saving)
    POST   /telegram/test              send a test message to every admin chat
    POST   /telegram/setup             register the webhook with Telegram
    POST   /telegram/unset-webhook     remove it
    GET    /telegram/status            bot identity + webhook health
    POST   /telegram/webhook/{secret}  update ingress (called by Telegram)
"""
from __future__ import annotations

import secrets as secrets_lib
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import AdminUser, DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import SlidingWindowLimiter
from app.db.base import utcnow
from app.db.models import BotUser
from app.schemas import (
    Message,
    TelegramConfigIn,
    TelegramConfigOut,
    TelegramTestResult,
    TelegramValidation,
)
from app.services import runtime_config
from app.services.audit import audit

router = APIRouter(prefix="/telegram", tags=["telegram"])
log = get_logger(__name__)

_webhook_limiter = SlidingWindowLimiter(600, 60)


# --------------------------------------------------------------------------- #
#  Update ingress (Telegram -> panel)
# --------------------------------------------------------------------------- #
@router.post("/webhook/{secret}", include_in_schema=False)
async def webhook(
    secret: str,
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> dict:
    """Telegram calls this endpoint. It must answer 200 fast, so updates are
    handled inline while every heavy job stays inside the worker."""
    if not runtime_config.is_enabled():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Bot is not configured")

    expected = runtime_config.webhook_secret() or "webhook"
    if secret != expected or (
        x_telegram_bot_api_secret_token and x_telegram_bot_api_secret_token != expected
    ):
        log.warning("telegram webhook rejected", extra={"path_secret_ok": secret == expected})
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Forbidden")

    allowed, _ = _webhook_limiter.check("telegram")
    if not allowed:
        # Never let Telegram retry-storm the service.
        return {"ok": True}

    update: dict[str, Any] = await request.json()

    from app.bot.router import handle_update
    from app.db.session import session_scope

    try:
        with session_scope() as db:
            handle_update(db, update)
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("telegram update failed", extra={"error": str(exc)})
    return {"ok": True}


# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
def _to_out(db, config: dict) -> TelegramConfigOut:
    token = config.get("token") or ""
    secret = config.get("webhook_secret") or ""
    return TelegramConfigOut(
        enabled=bool(config.get("enabled")) and bool(token),
        has_token=bool(token),
        token_hint=runtime_config.mask_secret(token) if token else None,
        bot_username=config.get("username") or None,
        webhook_secret_set=bool(secret),
        webhook_secret_hint=runtime_config.mask_secret(secret, keep=4) if secret else None,
        admin_ids=list(config.get("admin_ids") or []),
        auto_set_webhook=bool(config.get("auto_set_webhook")),
        webhook_url=runtime_config.webhook_url(db),
        token_source=runtime_config.source_of(runtime_config.TELEGRAM_TOKEN, db),
        admin_ids_source=runtime_config.source_of(runtime_config.TELEGRAM_ADMIN_IDS, db),
    )


@router.get("/config", response_model=TelegramConfigOut)
def get_config(db: DbSession, _: AdminUser) -> TelegramConfigOut:
    """Effective configuration with secrets masked - safe to render in the UI."""
    return _to_out(db, runtime_config.telegram_config(db, fresh=True))


@router.put("/config", response_model=TelegramConfigOut)
def update_config(payload: TelegramConfigIn, db: DbSession, actor: AdminUser) -> TelegramConfigOut:
    changed: list[str] = []

    if payload.clear_token:
        runtime_config.delete_value(db, runtime_config.TELEGRAM_TOKEN, commit=False)
        changed.append("bot_token:cleared")
    elif payload.bot_token:
        runtime_config.set_value(
            db, runtime_config.TELEGRAM_TOKEN, payload.bot_token, actor_id=actor.id, commit=False
        )
        changed.append("bot_token")

    if payload.bot_username is not None:
        runtime_config.set_value(
            db, runtime_config.TELEGRAM_USERNAME, payload.bot_username, actor_id=actor.id, commit=False
        )
        changed.append("bot_username")

    if payload.webhook_secret is not None:
        # An empty string means "generate a strong one for me".
        value = payload.webhook_secret or secrets_lib.token_hex(24)
        runtime_config.set_value(
            db, runtime_config.TELEGRAM_WEBHOOK_SECRET, value, actor_id=actor.id, commit=False
        )
        changed.append("webhook_secret")

    if payload.admin_ids is not None:
        runtime_config.set_value(
            db, runtime_config.TELEGRAM_ADMIN_IDS, payload.admin_ids, actor_id=actor.id, commit=False
        )
        changed.append("admin_ids")

    if payload.enabled is not None:
        runtime_config.set_value(db, runtime_config.TELEGRAM_ENABLED, payload.enabled, actor_id=actor.id, commit=False)
        changed.append("enabled")

    if payload.auto_set_webhook is not None:
        runtime_config.set_value(
            db, runtime_config.TELEGRAM_AUTO_WEBHOOK, payload.auto_set_webhook, actor_id=actor.id, commit=False
        )
        changed.append("auto_set_webhook")

    db.commit()
    runtime_config.invalidate()

    audit(
        db,
        action="settings.update",
        actor_id=actor.id,
        entity_type="telegram",
        meta={"changed": changed},
        commit=True,
    )
    return _to_out(db, runtime_config.telegram_config(db, fresh=True))


@router.delete("/config", response_model=Message)
def clear_config(db: DbSession, actor: AdminUser) -> Message:
    """Remove panel overrides so the environment variables win again."""
    removed = runtime_config.clear_telegram_overrides(db, actor_id=actor.id)
    audit(
        db,
        action="settings.update",
        actor_id=actor.id,
        entity_type="telegram",
        meta={"cleared": removed},
        commit=True,
    )
    return Message(detail=f"{removed} override(s) removed - environment values are active again")


@router.post("/generate-secret", response_model=dict)
def generate_secret(_: AdminUser) -> dict:
    return {"webhook_secret": secrets_lib.token_hex(24)}


# --------------------------------------------------------------------------- #
#  Validation & connectivity
# --------------------------------------------------------------------------- #
@router.post("/validate", response_model=TelegramValidation)
def validate(db: DbSession, _: AdminUser, token: Optional[str] = None) -> TelegramValidation:
    """Verify a token with getMe. Pass `?token=` to test before saving."""
    from app.bot.telegram import TelegramClient

    candidate = (token or "").strip() or runtime_config.bot_token(db)
    if not candidate:
        return TelegramValidation(ok=False, error="No token configured")

    info = TelegramClient(token=candidate).get_me()
    if not info:
        return TelegramValidation(
            ok=False, error="Telegram rejected this token - check it in @BotFather"
        )

    # Adopt the username Telegram reports when the operator has not set one.
    username = info.get("username")
    if username and not runtime_config.telegram_config(db, fresh=True).get("username"):
        runtime_config.set_value(db, runtime_config.TELEGRAM_USERNAME, username, commit=True)

    return TelegramValidation(
        ok=True,
        bot={
            "id": info.get("id"),
            "username": username,
            "first_name": info.get("first_name"),
            "can_join_groups": info.get("can_join_groups"),
            "can_read_all_group_messages": info.get("can_read_all_group_messages"),
        },
    )


@router.post("/test", response_model=TelegramTestResult)
def send_test(db: DbSession, actor: AdminUser) -> TelegramTestResult:
    """Send a ping to every configured admin chat."""
    from app.bot.telegram import TelegramClient

    config = runtime_config.telegram_config(db, fresh=True)
    if not config.get("token"):
        return TelegramTestResult(ok=False, error="Bot token is not configured")

    recipients = list(config.get("admin_ids") or [])
    if not recipients:
        return TelegramTestResult(
            ok=False,
            error="No admin IDs configured - send /id to the bot and paste the number here",
        )

    client = TelegramClient()
    delivered = 0
    for chat_id in recipients:
        if client.send_message(
            chat_id,
            "✅ <b>Test message</b>\n"
            f"Panel: {settings.panel_base_url}\n"
            f"Environment: {settings.environment}\n"
            f"Time: {utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
            "If you can read this, the bot is wired up correctly.",
        ):
            delivered += 1

    audit(
        db,
        action="settings.update",
        actor_id=actor.id,
        entity_type="telegram",
        status="success" if delivered else "failure",
        meta={"test": True, "delivered": delivered, "recipients": recipients},
        commit=True,
    )
    if not delivered:
        return TelegramTestResult(
            ok=False,
            recipients=recipients,
            error="Telegram did not accept the message - make sure each admin started the bot first",
        )
    return TelegramTestResult(ok=True, delivered=delivered, recipients=recipients)


@router.post("/setup", response_model=Message)
def setup_webhook(db: DbSession, _: AdminUser) -> Message:
    if not runtime_config.bot_token(db):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Save a bot token first")

    from app.bot.telegram import TelegramClient

    url = runtime_config.webhook_url(db)
    client = TelegramClient()
    if not client.set_webhook(url, secret_token=runtime_config.webhook_secret(db) or None):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Telegram rejected setWebhook (check the panel logs)")
    register_commands(client)
    return Message(detail=f"Webhook registered: {url}")


@router.post("/unset-webhook", response_model=Message)
def unset_webhook(db: DbSession, _: AdminUser) -> Message:
    from app.bot.telegram import TelegramClient

    TelegramClient().delete_webhook()
    return Message(detail="Webhook removed")


@router.get("/status")
def bot_status(db: DbSession, _: AdminUser) -> dict:
    from app.bot.telegram import TelegramClient

    config = runtime_config.telegram_config(db, fresh=True)
    client = TelegramClient()
    info = client.get_me() if config.get("token") else None
    hooked = client.get_webhook_info() if config.get("token") else None
    linked = db.execute(select(BotUser).where(BotUser.user_id.isnot(None))).scalars().all()
    return {
        "configured": bool(config.get("token")),
        "enabled": bool(config.get("enabled")) and bool(config.get("token")),
        "bot": info,
        "webhook": hooked,
        "expected_webhook": runtime_config.webhook_url(db),
        "linked_accounts": len(linked),
        "admin_ids": list(config.get("admin_ids") or []),
        "token_source": runtime_config.source_of(runtime_config.TELEGRAM_TOKEN, db),
        "checked_at": utcnow().isoformat(),
    }


def register_commands(client) -> None:
    """Register the Telegram command menu per language."""
    client.set_commands(
        [
            {"command": "start", "description": "منوی اصلی"},
            {"command": "account", "description": "وضعیت حساب"},
            {"command": "configs", "description": "کانفیگ‌های من"},
            {"command": "plans", "description": "خرید سرویس"},
            {"command": "wallet", "description": "کیف پول"},
            {"command": "support", "description": "پشتیبانی"},
            {"command": "lang", "description": "تغییر زبان"},
            {"command": "id", "description": "شناسه تلگرام من"},
            {"command": "help", "description": "راهنما"},
        ],
        language_code="fa",
    )
    client.set_commands(
        [
            {"command": "start", "description": "Main menu"},
            {"command": "account", "description": "Account status"},
            {"command": "configs", "description": "My configs"},
            {"command": "plans", "description": "Buy a plan"},
            {"command": "wallet", "description": "Wallet"},
            {"command": "support", "description": "Support"},
            {"command": "lang", "description": "Change language"},
            {"command": "id", "description": "My Telegram ID"},
            {"command": "help", "description": "Help"},
        ],
        language_code="en",
    )
