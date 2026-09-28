"""Runtime configuration editable from the panel UI.

Precedence
----------
    database (Setting row)  >  environment variable  >  built-in default

Why this exists
---------------
Bot credentials used to live only in environment variables, which means every
change required a redeploy. Operators want to paste a token into the panel and
have the bot come alive. This module is the single place that resolves those
values, so nothing else has to know where they came from.

Secrets
-------
`telegram.bot_token` and `telegram.webhook_secret` are stored encrypted with the
same Fernet key used for node tokens, and are never returned by any API.

Caching
-------
`TelegramClient` is constructed inside request handlers, background jobs and
alert delivery — sometimes without a database session in hand. A short-TTL
in-process cache keeps that hot path free of a query, while `invalidate()` makes
a UI change take effect immediately in the process that wrote it.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decrypt, encrypt
from app.db.models import Setting

log = get_logger(__name__)

# --------------------------------------------------------------------------- #
#  Keys
# --------------------------------------------------------------------------- #
TELEGRAM_TOKEN = "telegram.bot_token"
TELEGRAM_USERNAME = "telegram.bot_username"
TELEGRAM_WEBHOOK_SECRET = "telegram.webhook_secret"
TELEGRAM_ADMIN_IDS = "telegram.admin_ids"
TELEGRAM_ENABLED = "telegram.enabled"
TELEGRAM_AUTO_WEBHOOK = "telegram.auto_set_webhook"

SECRET_KEYS = {TELEGRAM_TOKEN, TELEGRAM_WEBHOOK_SECRET}

TELEGRAM_KEYS = (
    TELEGRAM_TOKEN,
    TELEGRAM_USERNAME,
    TELEGRAM_WEBHOOK_SECRET,
    TELEGRAM_ADMIN_IDS,
    TELEGRAM_ENABLED,
    TELEGRAM_AUTO_WEBHOOK,
)

DESCRIPTIONS = {
    TELEGRAM_TOKEN: "Bot token from @BotFather (stored encrypted)",
    TELEGRAM_USERNAME: "Bot @username, used for referral deep links",
    TELEGRAM_WEBHOOK_SECRET: "Random string Telegram echoes back on every update",
    TELEGRAM_ADMIN_IDS: "Telegram numeric IDs allowed into the admin console",
    TELEGRAM_ENABLED: "Master switch for the bot",
    TELEGRAM_AUTO_WEBHOOK: "Register the webhook automatically on boot",
}

# --------------------------------------------------------------------------- #
#  Environment fallbacks
# --------------------------------------------------------------------------- #
def _env_default(key: str) -> Any:
    return {
        TELEGRAM_TOKEN: settings.telegram_bot_token or "",
        TELEGRAM_USERNAME: settings.telegram_bot_username or "",
        TELEGRAM_WEBHOOK_SECRET: settings.telegram_webhook_secret or "",
        TELEGRAM_ADMIN_IDS: settings.telegram_admin_id_list,
        TELEGRAM_ENABLED: bool(settings.telegram_enabled),
        TELEGRAM_AUTO_WEBHOOK: bool(settings.telegram_auto_set_webhook),
    }.get(key)


# --------------------------------------------------------------------------- #
#  Raw access
# --------------------------------------------------------------------------- #
def _row(db: Session, key: str) -> Optional[Setting]:
    return db.get(Setting, key)


def get_value(db: Session, key: str, default: Any = None) -> Any:
    """Resolve one key with decryption applied for secrets."""
    row = _row(db, key)
    if row is None or row.value in (None, "", []):
        env = _env_default(key)
        return env if env not in (None, "", []) else default

    value = row.value
    if key in SECRET_KEYS and isinstance(value, str):
        if value.startswith("gAAAAA"):  # Fernet ciphertext marker
            decrypted = decrypt(value)
            if decrypted:
                return decrypted
            log.warning("stored secret could not be decrypted", extra={"key": key})
            return default
    return value


def set_value(
    db: Session,
    key: str,
    value: Any,
    *,
    actor_id: Optional[int] = None,
    commit: bool = True,
) -> Setting:
    stored = encrypt(value) if (key in SECRET_KEYS and isinstance(value, str) and value) else value
    row = _row(db, key)
    if row is None:
        row = Setting(
            key=key,
            value=stored,
            description=DESCRIPTIONS.get(key),
            is_secret=key in SECRET_KEYS,
        )
        db.add(row)
    else:
        row.value = stored
        row.is_secret = key in SECRET_KEYS
    row.updated_by_id = actor_id
    if commit:
        db.commit()
    invalidate()
    return row


def delete_value(db: Session, key: str, *, commit: bool = True) -> bool:
    row = _row(db, key)
    if row is None:
        return False
    db.delete(row)
    if commit:
        db.commit()
    invalidate()
    return True


def clear_telegram_overrides(db: Session, *, actor_id: Optional[int] = None) -> int:
    removed = 0
    for key in TELEGRAM_KEYS:
        if delete_value(db, key, commit=False):
            removed += 1
    if removed:
        db.commit()
    invalidate()
    return removed


# --------------------------------------------------------------------------- #
#  Telegram resolution (cached)
# --------------------------------------------------------------------------- #
_CACHE_TTL_SECONDS = 30.0
_cache: Optional[Dict[str, Any]] = None
_cache_at: float = 0.0


def invalidate() -> None:
    """Drop the cache so the next read reflects a just-saved change."""
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


def _load(db: Session) -> Dict[str, Any]:
    token = str(get_value(db, TELEGRAM_TOKEN, "") or "")
    secret = str(get_value(db, TELEGRAM_WEBHOOK_SECRET, "") or "")
    admin_ids = get_value(db, TELEGRAM_ADMIN_IDS, None)
    if not isinstance(admin_ids, list):
        admin_ids = settings.telegram_admin_id_list
    ids: List[int] = []
    for raw in admin_ids:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if settings.superadmin_telegram_id:
        ids.append(int(settings.superadmin_telegram_id))

    return {
        "token": token,
        "username": str(get_value(db, TELEGRAM_USERNAME, "") or ""),
        "webhook_secret": secret,
        "admin_ids": sorted(set(ids)),
        "enabled": bool(get_value(db, TELEGRAM_ENABLED, False)),
        "auto_set_webhook": bool(get_value(db, TELEGRAM_AUTO_WEBHOOK, False)),
    }


def _fallback() -> Dict[str, Any]:
    return {
        "token": settings.telegram_bot_token or "",
        "username": settings.telegram_bot_username or "",
        "webhook_secret": settings.telegram_webhook_secret or "",
        "admin_ids": settings.telegram_admin_id_list,
        "enabled": bool(settings.telegram_enabled),
        "auto_set_webhook": bool(settings.telegram_auto_set_webhook),
    }


def telegram_config(db: Optional[Session] = None, *, fresh: bool = False) -> Dict[str, Any]:
    """Return the effective bot configuration (never raises)."""
    global _cache, _cache_at

    now = time.monotonic()
    if not fresh and _cache is not None and (now - _cache_at) < _CACHE_TTL_SECONDS:
        return _cache

    try:
        if db is not None:
            config = _load(db)
        else:
            from app.db.session import session_scope

            with session_scope() as session:
                config = _load(session)
    except Exception as exc:  # pragma: no cover - startup / DB outage
        log.debug("runtime config fell back to environment", extra={"error": str(exc)})
        config = _fallback()

    _cache, _cache_at = config, now
    return config


def admin_ids(db: Optional[Session] = None) -> List[int]:
    return list(telegram_config(db).get("admin_ids") or [])


def bot_token(db: Optional[Session] = None) -> str:
    return str(telegram_config(db).get("token") or "")


def is_enabled(db: Optional[Session] = None) -> bool:
    config = telegram_config(db)
    return bool(config.get("enabled")) and bool(config.get("token"))


def webhook_secret(db: Optional[Session] = None) -> str:
    return str(telegram_config(db).get("webhook_secret") or "")


def webhook_url(db: Optional[Session] = None) -> str:
    base = settings.panel_base_url.rstrip("/")
    return f"{base}/api/v1/telegram/webhook/{webhook_secret(db) or 'webhook'}"


def source_of(key: str, db: Optional[Session] = None) -> str:
    """Where the effective value comes from — drives the UI hint."""
    if db is None:
        return "environment"
    return "database" if _row(db, key) is not None else "environment"


def mask_secret(value: str, keep: int = 6) -> str:
    if not value:
        return ""
    head, _, tail = value.partition(":")
    if tail:
        return f"{head}:{tail[:keep]}{'*' * 12}"
    return f"{value[:keep]}{'*' * 12}"
