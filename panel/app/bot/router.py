"""Telegram bot: user flows + a separate admin console.

Design notes
------------
* **Stateless navigation.** Every screen is reachable from `callback_data`
  (`m:*`, `cfg:*`, `buy:*`, `rn:*`, `a:*`, `pay:*`), so a restart or a second
  instance changes nothing for the user.
* **Few explicit states.** `BotUser.state` drives multi-step flows (rename,
  top-up, broadcast, user search); everything else is button-driven.
* **Ownership is re-checked.** A callback carrying a `service_id` is validated
  against `service.user_id` before anything is shown or mutated.
* **Preferences live in `state_data`** so a new toggle needs no migration.
* **Anti-spam.** A per-user token bucket drops bursts before they hit the DB.
"""
from __future__ import annotations

import html
import uuid as uuid_lib
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.bot.i18n import normalize, status_icon, status_label, tr
from app.bot.telegram import (
    TelegramClient,
    admin_menu,
    admin_user_keyboard,
    back,
    broadcast_keyboard,
    config_keyboard,
    confirm_keyboard,
    home,
    kb,
    language_picker,
    main_menu,
    method_keyboard,
    node_keyboard,
    payment_keyboard,
    plan_keyboard,
    plan_toggle_keyboard,
    services_keyboard,
    settings_menu,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import TokenBucket
from app.core.security import generate_sub_token, hash_password
from app.db.base import utcnow
from app.db.models import (
    Alert,
    BotUser,
    Node,
    NodeStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
    Plan,
    Role,
    Service,
    ServiceStatus,
    User,
    UserStatus,
)
from app.services.audit import audit
from app.services.billing import (
    BillingError,
    apply_referral,
    create_order,
    ensure_referral_code,
    fulfil_payment,
    pending_payments,
    reject_payment,
)
from app.services.provisioning import ProvisioningError, renew_service, set_status
from app.services.xray_links import build_link, format_bytes, qr_png

log = get_logger(__name__)
client = TelegramClient()
_bucket = TokenBucket(rate_per_second=1.2, burst=6)

DEFAULT_PREFS = {"notify_expiry": True, "notify_quota": True, "notify_news": True}


# --------------------------------------------------------------------------- #
#  Identity & preferences
# --------------------------------------------------------------------------- #
def _bot_user(db: Session, tg_user: dict) -> BotUser:
    tg_id = int(tg_user["id"])
    row = db.execute(select(BotUser).where(BotUser.telegram_id == tg_id)).scalar_one_or_none()
    if row is None:
        row = BotUser(
            telegram_id=tg_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
            language=normalize(tg_user.get("language_code")),
            is_admin=tg_id in settings.telegram_admin_id_list,
            state_data={"prefs": dict(DEFAULT_PREFS), "new": True},
        )
        db.add(row)
    else:
        row.username = tg_user.get("username") or row.username
        row.first_name = tg_user.get("first_name") or row.first_name
        row.is_admin = row.is_admin or tg_id in settings.telegram_admin_id_list
        if not (row.state_data or {}).get("prefs"):
            row.state_data = {**(row.state_data or {}), "prefs": dict(DEFAULT_PREFS)}
    row.last_seen_at = utcnow()

    if row.user_id is None:
        linked = db.execute(select(User).where(User.telegram_id == tg_id)).scalar_one_or_none()
        if linked:
            row.user_id = linked.id
    db.commit()
    return row


def _lang(bot_user: BotUser) -> str:
    return bot_user.language or "fa"


def _prefs(bot_user: BotUser) -> dict:
    return {**DEFAULT_PREFS, **((bot_user.state_data or {}).get("prefs") or {})}


def _set_prefs(db: Session, bot_user: BotUser, **changes: Any) -> dict:
    prefs = {**_prefs(bot_user), **changes}
    bot_user.state_data = {**(bot_user.state_data or {}), "prefs": prefs}
    db.commit()
    return prefs


def _is_admin(bot_user: BotUser) -> bool:
    # Admin IDs may be configured from the panel UI, so they are resolved at
    # call time rather than read once from the environment.
    try:
        from app.services import runtime_config

        if bot_user.telegram_id in runtime_config.admin_ids():
            return True
    except Exception:  # pragma: no cover - DB not ready
        if bot_user.telegram_id in settings.telegram_admin_id_list:
            return True
    if bot_user.user and bot_user.user.is_staff:
        return True
    return bool(bot_user.is_admin)


def _linked_user(db: Session, bot_user: BotUser) -> Optional[User]:
    return db.get(User, bot_user.user_id) if bot_user.user_id else None


def _ensure_user(db: Session, bot_user: BotUser) -> User:
    """Create a panel account for a Telegram user on first use."""
    user = _linked_user(db, bot_user)
    if user:
        return user

    base = (bot_user.username or f"tg{bot_user.telegram_id}").replace(" ", "")[:24] or f"tg{bot_user.telegram_id}"
    username = base
    suffix = 1
    while db.execute(select(User).where(User.username == username)).scalar_one_or_none():
        suffix += 1
        username = f"{base}{suffix}"

    user = User(
        uuid=str(uuid_lib.uuid4()),
        username=username,
        password_hash=hash_password(generate_sub_token()),
        role=Role.user,
        status=UserStatus.active,
        telegram_id=bot_user.telegram_id,
        telegram_username=bot_user.username,
        telegram_language=_lang(bot_user),
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    ensure_referral_code(db, user)
    bot_user.user_id = user.id
    db.commit()

    audit(
        db,
        action="user.create",
        actor_type="bot",
        actor_id=user.id,
        actor_label=f"tg:{bot_user.telegram_id}",
        entity_type="user",
        entity_id=user.id,
        meta={"source": "telegram"},
        commit=True,
    )
    return user


# --------------------------------------------------------------------------- #
#  Formatting helpers
# --------------------------------------------------------------------------- #
def _fmt_service(service: Service, lang: str) -> str:
    limit = format_bytes(service.traffic_limit_bytes) if service.traffic_limit_bytes else tr(lang, "common.unlimited")
    lines = [
        tr(lang, "configs.detail", label=html.escape(service.label), id=service.id),
        "",
        f"{tr(lang, 'configs.protocol')}: <code>{service.protocol.value}</code>",
        f"{tr(lang, 'configs.node')}: <code>{html.escape(service.node.name if service.node else '-')}</code>",
        f"{tr(lang, 'configs.status')}: <b>{status_label(lang, service.status.value)}</b>",
        f"{tr(lang, 'configs.usage')}: <b>{format_bytes(service.used_bytes)}</b> / <b>{limit}</b>",
    ]
    if service.expires_at:
        days = service.days_left
        suffix = tr(lang, "common.daysLeft", n=days) if days is not None and days > 0 else tr(lang, "common.expired")
        lines.append(f"{tr(lang, 'configs.expires')}: <b>{service.expires_at.strftime('%Y-%m-%d')}</b> ({suffix})")
    return "\n".join(lines)


def _usage_bar(percent: float, width: int = 10) -> str:
    filled = min(int(round((percent or 0) / 100 * width)), width)
    return "▰" * filled + "▱" * (width - filled)


def _payment_instructions(db: Session) -> str:
    from app.api.v1.settings import get_setting

    parts: list[str] = []
    card = get_setting(db, "payments.card_number")
    holder = get_setting(db, "payments.card_holder")
    crypto = get_setting(db, "payments.crypto_address")
    if card:
        parts.append(f"💳 <code>{html.escape(str(card))}</code>")
    if holder:
        parts.append(f"👤 <b>{html.escape(str(holder))}</b>")
    if crypto:
        parts.append(f"🪙 <code>{html.escape(str(crypto))}</code>")
    if not parts:
        fallback = get_setting(db, "payments.instructions") or settings.telegram_payment_instructions
        parts.append(html.escape(str(fallback)))
    return "\n".join(parts)


def _send(chat_id: int, message_id: Optional[int], text: str, keyboard: Optional[dict] = None) -> None:
    if message_id and client.edit_message(chat_id, message_id, text, keyboard=keyboard):
        return
    client.send_message(chat_id, text, keyboard=keyboard)


def _notify_admins(db: Session, text: str, keyboard: Optional[dict] = None) -> None:
    try:
        from app.services import runtime_config

        chat_ids = runtime_config.admin_ids(db)
    except Exception:  # pragma: no cover
        chat_ids = settings.telegram_admin_id_list
    for chat_id in chat_ids:
        client.send_message(chat_id, text, keyboard=keyboard)


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #
def handle_update(db: Session, update: dict) -> None:
    try:
        if "callback_query" in update:
            _handle_callback(db, update["callback_query"])
        elif "message" in update:
            _handle_message(db, update["message"])
        elif "pre_checkout_query" in update:
            client.answer_callback(update["pre_checkout_query"]["id"], "⏳")
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("bot handler crashed", extra={"error": str(exc)})
        db.rollback()


# --------------------------------------------------------------------------- #
#  Messages
# --------------------------------------------------------------------------- #
def _handle_message(db: Session, message: dict) -> None:
    tg_user = message.get("from") or {}
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    bot_user = _bot_user(db, tg_user)
    lang = _lang(bot_user)

    if not _bucket.allow(f"tg:{bot_user.telegram_id}"):
        client.send_message(chat_id, tr(lang, "common.tooFast"))
        return

    if message.get("photo"):
        _handle_receipt_photo(db, bot_user, chat_id, message)
        return

    if bot_user.state and _handle_state(db, bot_user, chat_id, text):
        return

    if not text:
        client.send_message(chat_id, tr(lang, "menu.title"), keyboard=main_menu(lang, _is_admin(bot_user)))
        return

    parts = text.split(maxsplit=1)
    command = parts[0].split("@")[0].lower()
    payload = parts[1].strip() if len(parts) > 1 else ""

    if command in ("/start", "/menu"):
        _screen_start(db, bot_user, chat_id, payload)
    elif command == "/help":
        client.send_message(chat_id, tr(lang, "help"), keyboard=home(lang))
    elif command == "/account":
        _screen_account(db, bot_user, chat_id)
    elif command == "/configs":
        _screen_configs(db, bot_user, chat_id)
    elif command == "/plans":
        _screen_plans(db, bot_user, chat_id)
    elif command == "/wallet":
        _screen_wallet(db, bot_user, chat_id)
    elif command == "/support":
        _screen_support(db, chat_id, None, lang)
    elif command in ("/id", "/myid"):
        _screen_identity(db, bot_user, chat_id, None)
    elif command == "/lang":
        client.send_message(chat_id, tr(lang, "lang.pick"), keyboard=language_picker())
    elif command == "/admin":
        if _is_admin(bot_user):
            client.send_message(chat_id, tr(lang, "admin.title"), keyboard=admin_menu(lang))
        else:
            client.send_message(chat_id, tr(lang, "admin.denied"))
    else:
        client.send_message(chat_id, tr(lang, "menu.title"), keyboard=main_menu(lang, _is_admin(bot_user)))


def _handle_state(db: Session, bot_user: BotUser, chat_id: int, text: str) -> bool:
    """Returns True when the message was consumed by a multi-step flow."""
    state = bot_user.state or ""
    lang = _lang(bot_user)

    if not text or text.startswith("/"):
        bot_user.state = None
        db.commit()
        return False

    if state == "rename":
        service_id = int((bot_user.state_data or {}).get("rename_id") or 0)
        user = _linked_user(db, bot_user)
        service = db.get(Service, service_id)
        bot_user.state = None
        bot_user.state_data = {k: v for k, v in (bot_user.state_data or {}).items() if k != "rename_id"}
        db.commit()
        if service and user and (service.user_id == user.id or _is_admin(bot_user)):
            service.label = text[:120]
            db.commit()
            client.send_message(chat_id, tr(lang, "configs.renamedTo", name=html.escape(service.label)))
            _screen_config_detail(db, bot_user, chat_id, None, service.id)
        else:
            client.send_message(chat_id, tr(lang, "common.notFound"))
        return True

    if state == "topup":
        bot_user.state = None
        db.commit()
        try:
            amount = max(float(text.replace(",", "").strip()), 1.0)
        except ValueError:
            client.send_message(chat_id, tr(lang, "common.error"))
            return True
        _create_topup(db, bot_user, chat_id, amount)
        return True

    if state.startswith("balance:"):
        target_id = int(state.split(":", 1)[1])
        bot_user.state = None
        db.commit()
        try:
            amount = float(text.replace(",", "").strip())
        except ValueError:
            client.send_message(chat_id, tr(lang, "common.error"))
            return True
        target = db.get(User, target_id)
        if target:
            target.balance += amount
            db.commit()
            client.send_message(chat_id, tr(lang, "admin.balanceAdded"), keyboard=admin_menu(lang))
        return True

    if state in ("broadcast_all", "broadcast_active"):
        only_active = state.endswith("active")
        bot_user.state = None
        db.commit()
        sent = _broadcast(db, text, only_active=only_active)
        client.send_message(chat_id, tr(lang, "admin.broadcastSent", n=sent), keyboard=admin_menu(lang))
        return True

    if state == "find":
        bot_user.state = None
        db.commit()
        _admin_find(db, bot_user, chat_id, text)
        return True

    bot_user.state = None
    db.commit()
    return False


def _handle_receipt_photo(db: Session, bot_user: BotUser, chat_id: int, message: dict) -> None:
    lang = _lang(bot_user)
    user = _linked_user(db, bot_user)
    if user is None:
        client.send_message(chat_id, tr(lang, "common.notFound"))
        return

    payment = db.execute(
        select(Payment)
        .where(
            Payment.user_id == user.id,
            Payment.status.in_([PaymentStatus.pending, PaymentStatus.awaiting_review]),
        )
        .order_by(Payment.created_at.desc())
    ).scalars().first()
    if payment is None:
        client.send_message(chat_id, tr(lang, "notify.noOrder"))
        return

    payment.receipt_file_id = message["photo"][-1]["file_id"]
    payment.status = PaymentStatus.awaiting_review
    db.commit()

    client.send_message(chat_id, tr(lang, "notify.receiptOk", ref=payment.reference))
    _notify_admins(
        db,
        f"🧾 <b>{html.escape(user.username)}</b> · <code>{payment.reference}</code>\n"
        f"<b>{payment.amount:g} {payment.currency}</b>",
        keyboard=payment_keyboard("en", payment.id),
    )


# --------------------------------------------------------------------------- #
#  Callbacks
# --------------------------------------------------------------------------- #
def _handle_callback(db: Session, cb: dict) -> None:
    data = cb.get("data") or ""
    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    bot_user = _bot_user(db, cb.get("from") or {})
    lang = _lang(bot_user)

    if not _bucket.allow(f"tg:{bot_user.telegram_id}"):
        client.answer_callback(cb["id"], tr(lang, "common.tooFast"), alert=True)
        return

    if data.startswith("lang:"):
        chosen = data.split(":", 1)[1]
        bot_user.language = chosen if chosen in ("fa", "en") else "fa"
        db.commit()
        # Create the panel account as soon as the user commits to using the bot,
        # so "My account" is never empty on first open.
        _ensure_user(db, bot_user)
        new_lang = _lang(bot_user)
        client.answer_callback(cb["id"], tr(new_lang, "lang.set"))
        _send(chat_id, message_id, tr(new_lang, "menu.title"), main_menu(new_lang, _is_admin(bot_user)))
        return

    if data.startswith("pay:"):
        _handle_payment_callback(db, cb, data, bot_user)
        return

    if data.startswith("a:"):
        if not _is_admin(bot_user):
            client.answer_callback(cb["id"], tr(lang, "admin.denied"), alert=True)
            return
        client.answer_callback(cb["id"])
        _admin_screen(db, bot_user, chat_id, message_id, data)
        return

    client.answer_callback(cb["id"])

    if data == "m:home":
        _send(chat_id, message_id, tr(lang, "menu.title"), main_menu(lang, _is_admin(bot_user)))
    elif data == "m:account":
        _screen_account(db, bot_user, chat_id, message_id)
    elif data == "m:configs":
        _screen_configs(db, bot_user, chat_id, message_id)
    elif data == "m:plans":
        _screen_plans(db, bot_user, chat_id, message_id)
    elif data == "m:renew":
        _screen_renew(db, bot_user, chat_id, message_id)
    elif data == "m:usage":
        _screen_usage(db, bot_user, chat_id, message_id)
    elif data == "m:wallet":
        _screen_wallet(db, bot_user, chat_id, message_id)
    elif data == "m:topup":
        bot_user.state = "topup"
        db.commit()
        _send(chat_id, message_id, tr(lang, "wallet.topupPrompt"), back(lang, "m:wallet"))
    elif data == "m:referral":
        _screen_referral(db, bot_user, chat_id, message_id)
    elif data == "m:support":
        _screen_support(db, chat_id, message_id, lang)
    elif data == "m:settings":
        _screen_settings(db, bot_user, chat_id, message_id)
    elif data == "set:lang":
        _send(chat_id, message_id, tr(lang, "lang.pick"), language_picker())
    elif data == "set:id":
        _screen_identity(db, bot_user, chat_id, message_id)
    elif data.startswith("notif:"):
        key = data.split(":", 1)[1]
        prefs = _set_prefs(db, bot_user, **{key: not _prefs(bot_user).get(key, True)})
        _send(chat_id, message_id, tr(lang, "settings.saved"), settings_menu(lang, prefs))
    elif data.startswith("cfg:"):
        _handle_config_callback(db, bot_user, chat_id, message_id, data)
    elif data.startswith("buy:"):
        _handle_buy_callback(db, bot_user, chat_id, message_id, data)
    elif data.startswith("rn:"):
        _handle_renew_callback(db, bot_user, chat_id, message_id, data)


# --------------------------------------------------------------------------- #
#  Start / menu screens
# --------------------------------------------------------------------------- #
def _screen_start(
    db: Session,
    bot_user: BotUser,
    chat_id: int,
    referral: str = "",
    edit_message_id: Optional[int] = None,
) -> None:
    lang = _lang(bot_user)

    # First contact: pick a language before anything else.
    if (bot_user.state_data or {}).get("new"):
        bot_user.state_data = {**(bot_user.state_data or {}), "new": False}
        db.commit()
        _send(chat_id, edit_message_id, tr(lang, "lang.pick"), language_picker())
        return

    user = _ensure_user(db, bot_user)
    if referral and not user.referred_by_id and apply_referral(db, user, referral):
        log.info("referral applied", extra={"user": user.username, "code": referral})

    text = tr(lang, "start.welcome", app=html.escape(settings.app_name))
    _send(chat_id, edit_message_id, text, main_menu(lang, _is_admin(bot_user)))


def _screen_account(db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int] = None) -> None:
    lang = _lang(bot_user)
    user = _ensure_user(db, bot_user)
    services = list(
        db.execute(
            select(Service).where(Service.user_id == user.id, Service.status != ServiceStatus.deleted)
        ).scalars()
    )
    active = [s for s in services if s.status == ServiceStatus.active]
    text = "\n".join(
        [
            tr(lang, "account.title"),
            "",
            f"{tr(lang, 'account.username')}: <code>{html.escape(user.username)}</code>",
            f"{tr(lang, 'account.id')}: <code>{user.id}</code>",
            f"{tr(lang, 'account.status')}: <b>{tr(lang, 'account.active') if user.status == UserStatus.active else tr(lang, 'account.disabled')}</b>",
            f"{tr(lang, 'account.balance')}: <b>{user.balance:.2f} {settings.default_currency}</b>",
            "",
            f"{tr(lang, 'account.configs')}: <b>{len(services)}</b> ({len(active)} {tr(lang, 'account.active')})",
            f"{tr(lang, 'account.usage')}: <b>{format_bytes(sum(s.used_bytes for s in services))}</b>",
            f"{tr(lang, 'account.joined')}: <b>{user.created_at.strftime('%Y-%m-%d')}</b>",
        ]
    )
    _send(chat_id, message_id, text, main_menu(lang, _is_admin(bot_user)))


def _screen_configs(db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int] = None) -> None:
    lang = _lang(bot_user)
    user = _ensure_user(db, bot_user)
    services = list(
        db.execute(
            select(Service)
            .where(Service.user_id == user.id, Service.status != ServiceStatus.deleted)
            .order_by(Service.id.desc())
        ).scalars()
    )
    if not services:
        _send(chat_id, message_id, tr(lang, "configs.empty"), main_menu(lang, _is_admin(bot_user)))
        return
    _send(chat_id, message_id, tr(lang, "configs.title"), services_keyboard(lang, services))


def _screen_config_detail(
    db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int], service_id: int
) -> None:
    lang = _lang(bot_user)
    user = _linked_user(db, bot_user)
    service = db.get(Service, service_id)
    if service is None or service.status == ServiceStatus.deleted:
        client.send_message(chat_id, tr(lang, "common.notFound"))
        return
    if user and service.user_id != user.id and not _is_admin(bot_user):
        client.send_message(chat_id, tr(lang, "common.denied"))
        return

    _send(chat_id, message_id, _fmt_service(service, lang), config_keyboard(lang, service.id, usable=service.is_usable))


def _handle_config_callback(db: Session, bot_user: BotUser, chat_id: int, message_id: int, data: str) -> None:
    lang = _lang(bot_user)
    parts = data.split(":")

    # `cfg:<id>` opens the detail screen; `cfg:<action>:<id>` performs an action.
    if len(parts) == 2 and parts[1].isdigit():
        _screen_config_detail(db, bot_user, chat_id, message_id, int(parts[1]))
        return

    action = parts[1] if len(parts) > 1 else ""
    service_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    user = _linked_user(db, bot_user)
    service = db.get(Service, service_id) if service_id else None
    if service is None or service.status == ServiceStatus.deleted:
        client.send_message(chat_id, tr(lang, "common.notFound"))
        return
    if user and service.user_id != user.id and not _is_admin(bot_user):
        client.send_message(chat_id, tr(lang, "common.denied"))
        return

    if action in ("link", "sub", "qr") and not service.is_usable:
        client.send_message(chat_id, tr(lang, "common.notUsable", status=status_label(lang, service.status.value)))
        return

    if action == "link":
        client.send_message(
            chat_id,
            tr(lang, "configs.link", link=html.escape(build_link(service))),
            keyboard=back(lang, f"cfg:{service.id}"),
        )
    elif action == "sub":
        client.send_message(
            chat_id,
            tr(
                lang,
                "configs.subTitle",
                url=html.escape(service.subscription_url),
                clash=html.escape(service.subscription_url) + "?format=clash",
            ),
            keyboard=back(lang, f"cfg:{service.id}"),
        )
    elif action == "qr":
        client.send_photo(
            chat_id,
            qr_png(build_link(service)),
            caption=f"<b>{html.escape(service.label)}</b>",
            keyboard=back(lang, f"cfg:{service.id}"),
        )
    elif action == "ren":
        plans = list(
            db.execute(select(Plan).where(Plan.is_active.is_(True)).order_by(Plan.sort_order, Plan.price)).scalars()
        )
        if not plans:
            client.send_message(chat_id, tr(lang, "plans.empty"))
            return
        rows = [[(f"{p.name} · {p.price:g} {p.currency}", f"rn:plan:{p.id}:{service.id}")] for p in plans[:12]]
        rows.append([(tr(lang, "btn.back"), f"cfg:{service.id}")])
        _send(chat_id, message_id, tr(lang, "plans.renewPlan", label=html.escape(service.label)), kb(rows))
    elif action == "name":
        bot_user.state = "rename"
        bot_user.state_data = {**(bot_user.state_data or {}), "rename_id": service.id}
        db.commit()
        _send(chat_id, message_id, tr(lang, "configs.renamePrompt"), back(lang, f"cfg:{service.id}"))
    elif action == "del":
        _send(
            chat_id,
            message_id,
            f"{tr(lang, 'configs.deleteConfirm')}\n\n<b>{html.escape(service.label)}</b>",
            confirm_keyboard(lang, f"cfg:delok:{service.id}", f"cfg:{service.id}"),
        )
    elif action == "delok":
        set_status(db, service, ServiceStatus.deleted, actor_id=user.id if user else None)
        _send(chat_id, message_id, tr(lang, "configs.deleted"), main_menu(lang, _is_admin(bot_user)))


# --------------------------------------------------------------------------- #
#  Purchase & renewal
# --------------------------------------------------------------------------- #
def _screen_plans(db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int] = None) -> None:
    lang = _lang(bot_user)
    _ensure_user(db, bot_user)
    plans = list(
        db.execute(
            select(Plan)
            .where(Plan.is_active.is_(True), Plan.is_public.is_(True))
            .order_by(Plan.sort_order, Plan.price)
        ).scalars()
    )
    if not plans:
        _send(chat_id, message_id, tr(lang, "plans.empty"), back(lang))
        return
    _send(chat_id, message_id, tr(lang, "plans.title"), plan_keyboard(lang, plans, "buy:plan"))


def _handle_buy_callback(db: Session, bot_user: BotUser, chat_id: int, message_id: int, data: str) -> None:
    lang = _lang(bot_user)
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    plan_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

    plan = db.get(Plan, plan_id)
    if plan is None or not plan.is_active:
        client.send_message(chat_id, tr(lang, "common.notFound"))
        return
    user = _ensure_user(db, bot_user)

    if action == "plan":
        traffic = f"{plan.traffic_gb:g} GB" if plan.traffic_gb else tr(lang, "common.unlimited")
        text = (
            tr(
                lang,
                "plans.detail",
                name=html.escape(plan.name),
                days=plan.duration_days,
                traffic=traffic,
                price=f"{plan.price:g}",
                currency=plan.currency,
            )
            + f"\n\n{tr(lang, 'plans.chooseMethod')}"
        )
        _send(chat_id, message_id, text, method_keyboard(lang, plan.id, allow_balance=user.balance >= plan.price))
        return

    if action == "bal":
        if user.balance < plan.price:
            client.send_message(
                chat_id,
                tr(lang, "plans.balanceLow", balance=f"{user.balance:.2f}", currency=plan.currency),
            )
            return
        user.balance -= plan.price
        db.commit()
        try:
            create_order(db, user=user, plan=plan, method=PaymentMethod.balance, purpose="purchase", auto_complete=True)
        except (BillingError, ProvisioningError) as exc:
            client.send_message(chat_id, f"{tr(lang, 'common.error')}\n<code>{html.escape(str(exc))}</code>")
            return
        _send(chat_id, message_id, tr(lang, "plans.balancePaid"), main_menu(lang, _is_admin(bot_user)))
        return

    if action in ("card", "crypto"):
        method = PaymentMethod.manual if action == "card" else PaymentMethod.crypto
        payment = create_order(db, user=user, plan=plan, method=method, purpose="purchase")
        text = "\n".join(
            [
                tr(lang, "plans.payNow"),
                "",
                f"{tr(lang, 'plans.orderRef')}: <code>{payment.reference}</code>",
                f"{tr(lang, 'plans.amount')}: <b>{payment.amount:g} {payment.currency}</b>",
                "",
                _payment_instructions(db),
                "",
                tr(lang, "plans.receiptHint"),
            ]
        )
        _send(chat_id, message_id, text, back(lang, "m:plans"))
        _notify_admins(
            db,
            f"🆕 <code>{payment.reference}</code> · <b>{html.escape(user.username)}</b> · "
            f"<b>{payment.amount:g} {payment.currency}</b>",
        )


def _screen_renew(db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int] = None) -> None:
    lang = _lang(bot_user)
    user = _ensure_user(db, bot_user)
    services = list(
        db.execute(
            select(Service).where(
                Service.user_id == user.id,
                Service.status.in_([ServiceStatus.active, ServiceStatus.expired, ServiceStatus.limited]),
            )
        ).scalars()
    )
    if not services:
        _send(chat_id, message_id, tr(lang, "plans.noService"), main_menu(lang, _is_admin(bot_user)))
        return
    _send(chat_id, message_id, tr(lang, "plans.renewTitle"), services_keyboard(lang, services, "rn"))


def _handle_renew_callback(db: Session, bot_user: BotUser, chat_id: int, message_id: int, data: str) -> None:
    lang = _lang(bot_user)
    parts = data.split(":")
    user = _linked_user(db, bot_user)

    if len(parts) == 2 and parts[1].isdigit():
        _screen_config_detail(db, bot_user, chat_id, message_id, int(parts[1]))
        return

    if len(parts) >= 4 and parts[1] == "plan":
        plan_id, service_id = int(parts[2]), int(parts[3])
        plan = db.get(Plan, plan_id)
        service = db.get(Service, service_id)
        if service is None or plan is None or (user and service.user_id != user.id):
            client.send_message(chat_id, tr(lang, "common.notFound"))
            return
        if user and plan.price > 0 and user.balance < plan.price:
            client.send_message(
                chat_id,
                tr(lang, "plans.balanceLow", balance=f"{user.balance:.2f}", currency=plan.currency),
            )
            return

        if user and plan.price > 0:
            user.balance -= plan.price
            db.commit()
        renew_service(db, service, days=plan.duration_days, reset_traffic=True, actor_id=user.id if user else None)
        if user and plan.price > 0:
            create_order(
                db,
                user=user,
                plan=plan,
                method=PaymentMethod.balance,
                purpose="renewal",
                service=service,
                auto_complete=False,
            )
        client.send_message(
            chat_id,
            f"{tr(lang, 'plans.activated')}\n\n<b>{html.escape(service.label)}</b>",
            keyboard=home(lang),
        )
        _screen_config_detail(db, bot_user, chat_id, None, service.id)
        return

    client.send_message(chat_id, tr(lang, "common.notFound"))


# --------------------------------------------------------------------------- #
#  Wallet / usage / referral / support / settings
# --------------------------------------------------------------------------- #
def _screen_usage(db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int] = None) -> None:
    lang = _lang(bot_user)
    user = _ensure_user(db, bot_user)
    services = list(db.execute(select(Service).where(Service.user_id == user.id)).scalars())
    if not services:
        _send(chat_id, message_id, tr(lang, "usage.empty"), main_menu(lang, _is_admin(bot_user)))
        return

    lines = [tr(lang, "usage.title"), ""]
    for service in services[:12]:
        limit = service.traffic_limit_bytes
        pct = service.usage_percent if limit else 0
        lines.append(
            f"{status_icon(service.status.value)} <b>{html.escape(service.label)}</b>\n"
            f"<code>{_usage_bar(pct)}</code> {format_bytes(service.used_bytes)}"
            f" / {format_bytes(limit) if limit else tr(lang, 'common.unlimited')}"
        )
    lines += ["", f"{tr(lang, 'usage.total')}: <b>{format_bytes(sum(s.used_bytes for s in services))}</b>"]
    _send(chat_id, message_id, "\n".join(lines), main_menu(lang, _is_admin(bot_user)))


def _screen_wallet(db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int] = None) -> None:
    lang = _lang(bot_user)
    user = _ensure_user(db, bot_user)
    payments = db.execute(
        select(Payment).where(Payment.user_id == user.id).order_by(Payment.created_at.desc()).limit(5)
    ).scalars().all()

    icon = {
        PaymentStatus.paid: "✅",
        PaymentStatus.pending: "⏳",
        PaymentStatus.awaiting_review: "🕒",
        PaymentStatus.failed: "❌",
        PaymentStatus.refunded: "↩️",
        PaymentStatus.canceled: "🚫",
    }
    lines = [
        tr(lang, "wallet.title"),
        "",
        f"{tr(lang, 'wallet.balance')}: <b>{user.balance:.2f} {settings.default_currency}</b>",
        f"{tr(lang, 'wallet.earnings')}: <b>{user.referral_earnings:.2f}</b>",
        "",
        f"<b>{tr(lang, 'wallet.recent')}</b>",
    ]
    for payment in payments:
        lines.append(
            f"{icon.get(payment.status, '•')} <code>{payment.reference}</code> — {payment.amount:g} {payment.currency}"
        )
    if not payments:
        lines.append(tr(lang, "wallet.none"))

    _send(
        chat_id,
        message_id,
        "\n".join(lines),
        kb([[(tr(lang, "wallet.topup"), "m:topup")], [(tr(lang, "btn.home"), "m:home")]]),
    )


def _create_topup(db: Session, bot_user: BotUser, chat_id: int, amount: float) -> None:
    lang = _lang(bot_user)
    user = _ensure_user(db, bot_user)
    plan = Plan(
        code=f"topup-{uuid_lib.uuid4().hex[:8]}",
        name="Wallet top-up",
        price=amount,
        currency=settings.default_currency,
        duration_days=1,
        traffic_gb=0,
        is_active=False,
        is_public=False,
    )
    db.add(plan)
    db.flush()
    payment = create_order(db, user=user, plan=plan, method=PaymentMethod.manual, purpose="topup")
    _send(
        chat_id,
        None,
        tr(
            lang,
            "wallet.topupCreated",
            ref=payment.reference,
            amount=f"{amount:g}",
            currency=payment.currency,
            instructions=_payment_instructions(db),
        ),
        back(lang, "m:wallet"),
    )
    _notify_admins(
        db,
        f"💰 <code>{payment.reference}</code> · <b>{html.escape(user.username)}</b> · "
        f"<b>{amount:g} {payment.currency}</b>",
        keyboard=payment_keyboard("en", payment.id),
    )


def _screen_referral(db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int] = None) -> None:
    lang = _lang(bot_user)
    user = _ensure_user(db, bot_user)
    code = ensure_referral_code(db, user)
    invited = db.execute(select(func.count(User.id)).where(User.referred_by_id == user.id)).scalar_one()

    # The bot username can be configured from the panel, so resolve it at runtime.
    try:
        from app.services import runtime_config

        bot_username = runtime_config.telegram_config(db).get("username") or settings.telegram_bot_username
    except Exception:  # pragma: no cover
        bot_username = settings.telegram_bot_username

    link = f"https://t.me/{bot_username}?start={code}" if bot_username else code
    text = "\n".join(
        [
            tr(lang, "referral.title"),
            "",
            f"{tr(lang, 'referral.code')}: <code>{code}</code>",
            f"{tr(lang, 'referral.link')}: <code>{html.escape(link)}</code>",
            "",
            f"{tr(lang, 'referral.invited')}: <b>{invited}</b>",
            f"{tr(lang, 'referral.earned')}: <b>{user.referral_earnings:.2f}</b>",
            "",
            tr(lang, "referral.hint", percent=settings.referral_bonus_percent),
        ]
    )
    _send(chat_id, message_id, text, main_menu(lang, _is_admin(bot_user)))


def _screen_support(db: Session, chat_id: int, message_id: Optional[int], lang: str) -> None:
    from app.api.v1.settings import get_setting

    url = get_setting(db, "branding.support_url") or settings.panel_base_url
    text = (
        f"{tr(lang, 'support.title')}\n\n{tr(lang, 'support.faq')}\n\n"
        f"{tr(lang, 'support.contact')}: {html.escape(str(url))}"
    )
    _send(chat_id, message_id, text, back(lang))


def _screen_settings(db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int] = None) -> None:
    lang = _lang(bot_user)
    _send(chat_id, message_id, tr(lang, "settings.title"), settings_menu(lang, _prefs(bot_user)))


def _screen_identity(db: Session, bot_user: BotUser, chat_id: int, message_id: Optional[int] = None) -> None:
    """Lets an operator discover their numeric chat ID to paste into the panel."""
    lang = _lang(bot_user)
    is_admin = _is_admin(bot_user)
    text = "\n".join(
        [
            tr(lang, "identity.title"),
            "",
            f"{tr(lang, 'identity.chatId')}: <code>{bot_user.telegram_id}</code>",
            f"{tr(lang, 'identity.username')}: <code>@{html.escape(bot_user.username or '-')}</code>",
            f"{tr(lang, 'identity.role')}: <b>{tr(lang, 'identity.admin') if is_admin else tr(lang, 'identity.customer')}</b>",
            "",
            tr(lang, "identity.hint"),
        ]
    )
    keyboard = kb([[(tr(lang, "btn.home"), "m:home")]]) if not is_admin else kb(
        [[(tr(lang, "menu.admin"), "a:home")], [(tr(lang, "btn.home"), "m:home")]]
    )
    _send(chat_id, message_id, text, keyboard)


# --------------------------------------------------------------------------- #
#  Payment review
# --------------------------------------------------------------------------- #
def _handle_payment_callback(db: Session, cb: dict, data: str, bot_user: BotUser) -> None:
    lang = _lang(bot_user)
    if not _is_admin(bot_user):
        client.answer_callback(cb["id"], tr(lang, "admin.denied"), alert=True)
        return

    parts = data.split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        client.answer_callback(cb["id"], tr(lang, "common.error"), alert=True)
        return

    action, payment_id = parts[1], int(parts[2])
    payment = db.get(Payment, payment_id)
    if payment is None:
        client.answer_callback(cb["id"], tr(lang, "common.notFound"), alert=True)
        return

    chat_id = cb["message"]["chat"]["id"]
    message_id = cb["message"]["message_id"]
    admin_user = _linked_user(db, bot_user)

    if action == "ok":
        if payment.status == PaymentStatus.paid:
            client.answer_callback(cb["id"], "✅", alert=True)
            return
        try:
            fulfil_payment(db, payment, actor_id=admin_user.id if admin_user else None, actor_type="bot")
        except (BillingError, ProvisioningError) as exc:
            client.answer_callback(cb["id"], tr(lang, "common.error"), alert=True)
            client.send_message(chat_id, f"❌ <code>{html.escape(str(exc))}</code>")
            return
        client.answer_callback(cb["id"], "✅")
        client.edit_message(chat_id, message_id, f"✅ <code>{payment.reference}</code>")
        if payment.user and payment.user.telegram_id:
            user_lang = payment.user.telegram_language or "fa"
            client.send_message(payment.user.telegram_id, tr(user_lang, "plans.activated"), keyboard=main_menu(user_lang))
    else:
        reject_payment(db, payment, reason="rejected via bot", actor_id=admin_user.id if admin_user else None)
        client.answer_callback(cb["id"], "❌")
        client.edit_message(chat_id, message_id, f"❌ <code>{payment.reference}</code>")
        if payment.user and payment.user.telegram_id:
            user_lang = payment.user.telegram_language or "fa"
            client.send_message(payment.user.telegram_id, tr(user_lang, "notify.rejected", ref=payment.reference))


# --------------------------------------------------------------------------- #
#  Admin console
# --------------------------------------------------------------------------- #
def _admin_screen(db: Session, bot_user: BotUser, chat_id: int, message_id: int, data: str) -> None:
    lang = _lang(bot_user)
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else "home"

    def arg(index: int, cast=int):
        try:
            return cast(parts[index])
        except (IndexError, ValueError):
            return None

    if action == "home":
        _send(chat_id, message_id, tr(lang, "admin.title"), admin_menu(lang))

    elif action == "stats":
        today = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        users_total = db.execute(select(func.count(User.id))).scalar_one()
        services_total = db.execute(select(func.count(Service.id))).scalar_one()
        services_active = db.execute(
            select(func.count(Service.id)).where(Service.status == ServiceStatus.active)
        ).scalar_one()
        nodes_online = db.execute(select(func.count(Node.id)).where(Node.status == NodeStatus.online)).scalar_one()
        nodes_total = db.execute(select(func.count(Node.id))).scalar_one()
        pending = db.execute(
            select(func.count(Payment.id)).where(
                Payment.status.in_([PaymentStatus.pending, PaymentStatus.awaiting_review])
            )
        ).scalar_one()
        alerts = db.execute(select(func.count(Alert.id)).where(Alert.is_active.is_(True))).scalar_one()
        revenue = db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == PaymentStatus.paid, Payment.paid_at >= today
            )
        ).scalar_one()
        _send(
            chat_id,
            message_id,
            tr(
                lang,
                "admin.statsBody",
                users=users_total,
                active=services_active,
                total=services_total,
                nodesOnline=nodes_online,
                nodesTotal=nodes_total,
                pending=pending,
                alerts=alerts,
                revenue=f"{float(revenue or 0):.2f}",
                currency=settings.default_currency,
            ),
            admin_menu(lang),
        )

    elif action == "users":
        rows = db.execute(select(User).order_by(User.created_at.desc()).limit(10)).scalars().all()
        if not rows:
            _send(chat_id, message_id, tr(lang, "admin.findEmpty"), admin_menu(lang))
            return
        rows_kb = [[(f"{u.username} · {u.status.value}", f"a:u:{u.id}")] for u in rows]
        rows_kb.append([(tr(lang, "btn.back"), "a:home")])
        _send(chat_id, message_id, f"{tr(lang, 'admin.usersBody')}\n\n{tr(lang, 'admin.usersHint')}", kb(rows_kb))

    elif action == "u":
        target = db.get(User, arg(2) or 0)
        if target is None:
            client.send_message(chat_id, tr(lang, "common.notFound"))
            return
        services = db.execute(select(Service).where(Service.user_id == target.id)).scalars().all()
        _send(
            chat_id,
            message_id,
            tr(
                lang,
                "admin.userCard",
                id=target.id,
                username=html.escape(target.username),
                status=status_label(lang, target.status.value),
                role=target.role.value,
                balance=f"{target.balance:.2f}",
                currency=settings.default_currency,
                configs=len(services),
                usage=format_bytes(sum(s.used_bytes for s in services)),
            ),
            admin_user_keyboard(lang, target.id),
        )

    elif action == "bal":
        target = db.get(User, arg(2) or 0)
        amount = arg(3, float) or 0.0
        if target and amount:
            target.balance += amount
            db.commit()
        client.send_message(chat_id, tr(lang, "admin.balanceAdded"), keyboard=admin_menu(lang))

    elif action == "renew":
        target = db.get(User, arg(2) or 0)
        if target:
            for service in db.execute(
                select(Service).where(Service.user_id == target.id, Service.status != ServiceStatus.deleted)
            ).scalars():
                renew_service(db, service, days=30, reset_traffic=True, actor_id=bot_user.user_id)
        client.send_message(chat_id, tr(lang, "admin.userRenewed"), keyboard=admin_menu(lang))

    elif action == "toggle":
        target = db.get(User, arg(2) or 0)
        if target:
            target.status = UserStatus.disabled if target.status == UserStatus.active else UserStatus.active
            db.commit()
        client.send_message(chat_id, tr(lang, "admin.userToggled"), keyboard=admin_menu(lang))

    elif action == "pay":
        rows = pending_payments(db, limit=10)
        if not rows:
            _send(chat_id, message_id, tr(lang, "admin.noPayments"), admin_menu(lang))
            return
        for payment in rows[:5]:
            client.send_message(
                chat_id,
                f"🧾 <code>{payment.reference}</code>\n"
                f"👤 <code>{html.escape(payment.user.username if payment.user else '-')}</code>\n"
                f"💰 <b>{payment.amount:g} {payment.currency}</b>\n"
                f"📌 {payment.purpose} · {payment.status.value}",
                keyboard=payment_keyboard(lang, payment.id),
            )
            if payment.receipt_file_id:
                client.send_photo(chat_id, payment.receipt_file_id, caption=f"🧾 {payment.reference}")
        _send(chat_id, message_id, tr(lang, "admin.paymentsBody", n=len(rows)), admin_menu(lang))

    elif action == "nodes":
        nodes = db.execute(select(Node).order_by(Node.name)).scalars().all()
        if not nodes:
            _send(chat_id, message_id, tr(lang, "admin.noNodes"), admin_menu(lang))
            return
        rows_kb = [
            [
                (
                    f"{'🟢' if n.status == NodeStatus.online else '🔴'} {n.name} · CPU {n.cpu_percent or 0:.0f}%",
                    f"a:nhealth:{n.id}",
                )
            ]
            for n in nodes[:10]
        ]
        rows_kb.append([(tr(lang, "btn.back"), "a:home")])
        _send(chat_id, message_id, tr(lang, "admin.nodesBody"), kb(rows_kb))

    elif action == "nhealth":
        node = db.get(Node, arg(2) or 0)
        if node is None:
            client.send_message(chat_id, tr(lang, "common.notFound"))
            return
        from app.services.node_client import NodeClient

        resp = NodeClient(node).health()
        if resp.ok:
            data_ = resp.data
            node.status = NodeStatus.online
            node.last_heartbeat_at = utcnow()
            node.cpu_percent = data_.get("cpu_percent")
            node.memory_percent = data_.get("memory_percent")
            node.disk_percent = data_.get("disk_percent")
            node.xray_version = data_.get("xray_version") or node.xray_version
            node.online_users = data_.get("active_users")
            db.commit()
            text = tr(
                lang,
                "admin.nodeHealth",
                name=html.escape(node.name),
                status=node.status.value,
                xray="✅" if data_.get("xray_running") else "❌",
                cpu=f"{data_.get('cpu_percent') or 0:.0f}",
                ram=f"{data_.get('memory_percent') or 0:.0f}",
                disk=f"{data_.get('disk_percent') or 0:.0f}",
                users=data_.get("active_users") or 0,
            )
        else:
            node.status = NodeStatus.offline
            db.commit()
            text = f"🔴 <b>{html.escape(node.name)}</b>\n<code>{html.escape(resp.error or 'unreachable')}</code>"
        _send(chat_id, message_id, text, node_keyboard(lang, node.id))

    elif action == "nsync":
        node = db.get(Node, arg(2) or 0)
        if node is None:
            client.send_message(chat_id, tr(lang, "common.notFound"))
            return
        from app.services.provisioning import sync_node

        result = sync_node(db, node, actor_id=bot_user.user_id)
        if result["ok"]:
            client.send_message(chat_id, tr(lang, "admin.nodeSynced", n=result["applied"]), keyboard=admin_menu(lang))
        else:
            client.send_message(
                chat_id,
                f"❌ <code>{html.escape('; '.join(result['errors'])[:300])}</code>",
                keyboard=admin_menu(lang),
            )

    elif action == "report":
        by_status = db.execute(select(Service.status, func.count(Service.id)).group_by(Service.status)).all()
        total_traffic = db.execute(
            select(func.coalesce(func.sum(Service.used_up_bytes + Service.used_down_bytes), 0))
        ).scalar_one()
        lines = [tr(lang, "admin.reportBody"), ""]
        for row in by_status:
            key = row[0].value if hasattr(row[0], "value") else str(row[0])
            lines.append(f"• {status_label(lang, key)}: <b>{row[1]}</b>")
        lines += ["", f"📶 {tr(lang, 'usage.total')}: <b>{format_bytes(int(total_traffic))}</b>"]
        _send(chat_id, message_id, "\n".join(lines), admin_menu(lang))

    elif action == "alerts":
        rows = db.execute(
            select(Alert).where(Alert.is_active.is_(True)).order_by(Alert.created_at.desc()).limit(10)
        ).scalars().all()
        if not rows:
            _send(chat_id, message_id, tr(lang, "admin.noAlerts"), admin_menu(lang))
            return
        rows_kb = [
            [(f"{'🔴' if a.level.value == 'critical' else '⚠️'} {a.title[:38]} (×{a.occurrences})", f"a:ack:{a.id}")]
            for a in rows[:10]
        ]
        rows_kb.append([(tr(lang, "btn.back"), "a:home")])
        _send(chat_id, message_id, tr(lang, "admin.alertsBody"), kb(rows_kb))

    elif action == "ack":
        alert = db.get(Alert, arg(2) or 0)
        if alert:
            from app.services.alerts import acknowledge

            acknowledge(db, alert, user_id=bot_user.user_id or 0)
        client.send_message(chat_id, tr(lang, "admin.alertAcked"), keyboard=admin_menu(lang))

    elif action == "plans":
        plans = db.execute(select(Plan).order_by(Plan.sort_order, Plan.price).limit(12)).scalars().all()
        if not plans:
            _send(chat_id, message_id, tr(lang, "plans.empty"), admin_menu(lang))
            return
        _send(chat_id, message_id, tr(lang, "admin.plansBody"), plan_toggle_keyboard(lang, plans))

    elif action == "ptoggle":
        plan = db.get(Plan, arg(2) or 0)
        if plan:
            plan.is_active = not plan.is_active
            db.commit()
        client.send_message(chat_id, tr(lang, "admin.planToggled"), keyboard=admin_menu(lang))

    elif action == "find":
        bot_user.state = "find"
        db.commit()
        _send(chat_id, message_id, tr(lang, "admin.findPrompt"), back(lang, "a:home"))

    elif action == "bc":
        _send(chat_id, message_id, tr(lang, "admin.broadcastTarget"), broadcast_keyboard(lang))

    elif action in ("bcall", "bcactive"):
        bot_user.state = "broadcast_active" if action == "bcactive" else "broadcast_all"
        db.commit()
        _send(chat_id, message_id, tr(lang, "admin.broadcastPrompt"), back(lang, "a:home"))

    elif action == "backup":
        from app.services.backup import create_backup

        try:
            record = create_backup(db, label="telegram")
            text = tr(lang, "admin.backupDone", file=html.escape(record.filename), size=format_bytes(record.size_bytes))
        except Exception as exc:  # pragma: no cover - defensive
            text = tr(lang, "admin.backupFailed", error=html.escape(str(exc)[:200]))
        _send(chat_id, message_id, text, admin_menu(lang))

    else:
        _send(chat_id, message_id, tr(lang, "admin.title"), admin_menu(lang))


def _admin_find(db: Session, bot_user: BotUser, chat_id: int, query: str) -> None:
    lang = _lang(bot_user)
    term = query.strip()
    like = f"%{term}%"
    stmt = select(User).where(or_(User.username.ilike(like), User.email.ilike(like)))
    if term.isdigit():
        stmt = select(User).where(or_(User.id == int(term), User.username.ilike(like)))
    rows = db.execute(stmt.limit(8)).scalars().all()

    if not rows:
        client.send_message(chat_id, tr(lang, "admin.findEmpty"), keyboard=admin_menu(lang))
        return
    for user in rows:
        services = db.execute(select(Service).where(Service.user_id == user.id)).scalars().all()
        client.send_message(
            chat_id,
            tr(
                lang,
                "admin.userCard",
                id=user.id,
                username=html.escape(user.username),
                status=status_label(lang, user.status.value),
                role=user.role.value,
                balance=f"{user.balance:.2f}",
                currency=settings.default_currency,
                configs=len(services),
                usage=format_bytes(sum(s.used_bytes for s in services)),
            ),
            keyboard=admin_user_keyboard(lang, user.id),
        )


def _broadcast(db: Session, text: str, *, only_active: bool = False) -> int:
    stmt = select(BotUser).where(BotUser.is_blocked.is_(False))
    if only_active:
        active_ids = select(Service.user_id).where(Service.status == ServiceStatus.active).scalar_subquery()
        stmt = stmt.where(BotUser.user_id.in_(active_ids))
    rows = db.execute(stmt).scalars().all()

    sent = 0
    for row in rows:
        if client.send_message(row.telegram_id, text):
            sent += 1
    audit(
        db,
        action="bot.broadcast",
        actor_type="bot",
        meta={"recipients": sent, "candidates": len(rows), "only_active": only_active},
        commit=True,
    )
    return sent


# --------------------------------------------------------------------------- #
#  Outbound notifications (used by the scheduler)
# --------------------------------------------------------------------------- #
def _user_prefs(db: Session, user: User) -> dict:
    if not user.telegram_id:
        return {}
    row = db.execute(select(BotUser).where(BotUser.telegram_id == user.telegram_id)).scalar_one_or_none()
    return _prefs(row) if row else dict(DEFAULT_PREFS)


def notify_user(
    db: Session, user: User, text: str, keyboard: Optional[dict] = None, *, pref: Optional[str] = None
) -> bool:
    if not user.telegram_id or not client.configured:
        return False
    if pref and not _user_prefs(db, user).get(pref, True):
        return False
    lang = user.telegram_language or "fa"
    return client.send_message(user.telegram_id, text, keyboard=keyboard or main_menu(lang)) is not None


def notify_quota_reached(db: Session, service: Service) -> bool:
    if not service.user:
        return False
    lang = service.user.telegram_language or "fa"
    return notify_user(
        db, service.user,
        tr(lang, "notify.quota", label=html.escape(service.label)),
        back(lang, "m:renew"),
        pref="notify_quota",
    )


def notify_expiring(db: Session, service: Service) -> bool:
    if not service.user:
        return False
    lang = service.user.telegram_language or "fa"
    return notify_user(
        db, service.user,
        tr(lang, "notify.expiring", label=html.escape(service.label), days=service.days_left),
        back(lang, "m:renew"),
        pref="notify_expiry",
    )


def notify_expired(db: Session, service: Service) -> bool:
    if not service.user:
        return False
    lang = service.user.telegram_language or "fa"
    return notify_user(
        db, service.user,
        tr(lang, "notify.expired", label=html.escape(service.label)),
        back(lang, "m:renew"),
        pref="notify_expiry",
    )
