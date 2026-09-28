"""Thin, dependency-free Telegram Bot API client + inline keyboard builders.

Deliberately raw HTTP instead of a framework: webhook mode on Railway/Render
needs no long-polling loop, no extra process and no framework upgrade risk.
Every call is best-effort — a Telegram outage logs and returns None instead of
breaking the web request that triggered it.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional, Sequence

import httpx

from app.bot.i18n import tr
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

API_BASE = "https://api.telegram.org"


def _resolve_token() -> str:
    """Panel-stored token first, environment variable as fallback."""
    try:
        from app.services import runtime_config

        token = runtime_config.bot_token()
        if token:
            return token
    except Exception:  # pragma: no cover - import/DB not ready
        pass
    return settings.telegram_bot_token or ""


class TelegramClient:
    def __init__(self, token: Optional[str] = None, timeout: float = 20.0) -> None:
        # Token precedence: explicit argument -> panel runtime config -> env var.
        # Resolved lazily so a token saved in the UI works without a redeploy.
        if token:
            self.token = token
        else:
            self.token = _resolve_token()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token)

    # ------------------------------------------------------------------ core
    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    def call(
        self,
        method: str,
        payload: Optional[dict] = None,
        *,
        files: Optional[dict] = None,
    ) -> Optional[dict]:
        if not self.configured:
            log.debug("telegram call skipped: no token", extra={"method": method})
            return None
        try:
            with httpx.Client(timeout=self.timeout) as client:
                if files:
                    resp = client.post(self._url(method), data=_stringify(payload or {}), files=files)
                else:
                    resp = client.post(self._url(method), json=payload or {})
            body = resp.json()
            if not body.get("ok"):
                # "message is not modified" happens when a user taps the same
                # button twice; that is normal and must not raise noise.
                description = str(body.get("description", ""))
                if "not modified" not in description.lower():
                    log.warning(
                        "telegram api error",
                        extra={"method": method, "status": resp.status_code, "description": description[:200]},
                    )
                return None
            return body.get("result")
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("telegram transport error", extra={"method": method, "error": str(exc)})
            return None

    # -------------------------------------------------------------- messaging
    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        keyboard: Optional[dict] = None,
        disable_preview: bool = True,
    ) -> Optional[dict]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
            "parse_mode": parse_mode,
            "link_preview_options": {"is_disabled": disable_preview},
        }
        if keyboard:
            payload["reply_markup"] = keyboard
        return self.call("sendMessage", payload)

    def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
        keyboard: Optional[dict] = None,
    ) -> Optional[dict]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4096],
            "parse_mode": parse_mode,
            "link_preview_options": {"is_disabled": True},
        }
        if keyboard is not None:
            payload["reply_markup"] = keyboard
        return self.call("editMessageText", payload)

    def edit_reply_markup(self, chat_id: int, message_id: int, keyboard: Optional[dict] = None) -> Optional[dict]:
        return self.call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": keyboard or {"inline_keyboard": []}},
        )

    def delete_message(self, chat_id: int, message_id: int) -> Optional[dict]:
        return self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    def send_photo(
        self,
        chat_id: int,
        photo: bytes | str,
        *,
        caption: Optional[str] = None,
        keyboard: Optional[dict] = None,
        parse_mode: str = "HTML",
    ) -> Optional[dict]:
        if isinstance(photo, bytes):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "caption": (caption or "")[:1024],
                "parse_mode": parse_mode,
            }
            if keyboard:
                payload["reply_markup"] = json.dumps(keyboard)
            return self.call("sendPhoto", payload, files={"photo": ("qr.png", photo, "image/png")})
        payload = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": (caption or "")[:1024],
            "parse_mode": parse_mode,
        }
        if keyboard:
            payload["reply_markup"] = keyboard
        return self.call("sendPhoto", payload)

    def send_chat_action(self, chat_id: int, action: str = "typing") -> Optional[dict]:
        return self.call("sendChatAction", {"chat_id": chat_id, "action": action})

    def answer_callback(self, callback_id: str, text: str = "", *, alert: bool = False) -> Optional[dict]:
        return self.call(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text[:200], "show_alert": alert},
        )

    def get_file(self, file_id: str) -> Optional[str]:
        result = self.call("getFile", {"file_id": file_id})
        if not result:
            return None
        return f"{API_BASE}/file/bot{self.token}/{result.get('file_path')}"

    # ------------------------------------------------------------- lifecycle
    def get_me(self) -> Optional[dict]:
        return self.call("getMe")

    def set_webhook(self, url: str, *, secret_token: Optional[str] = None) -> bool:
        payload: dict[str, Any] = {
            "url": url,
            "allowed_updates": ["message", "callback_query", "pre_checkout_query", "my_chat_member"],
            "drop_pending_updates": True,
            "max_connections": 40,
        }
        if secret_token:
            payload["secret_token"] = secret_token
        return self.call("setWebhook", payload) is not None

    def delete_webhook(self) -> bool:
        return self.call("deleteWebhook", {"drop_pending_updates": True}) is not None

    def get_webhook_info(self) -> Optional[dict]:
        return self.call("getWebhookInfo")

    def set_commands(self, commands: Sequence[dict], *, language_code: Optional[str] = None) -> bool:
        payload: dict[str, Any] = {"commands": list(commands)}
        if language_code:
            payload["language_code"] = language_code
        return self.call("setMyCommands", payload) is not None


def _stringify(payload: dict) -> dict:
    out: dict[str, Any] = {}
    for key, value in payload.items():
        out[key] = json.dumps(value) if isinstance(value, (dict, list)) else value
    return out


# --------------------------------------------------------------------------- #
#  Keyboard builders
# --------------------------------------------------------------------------- #
def kb(rows: Iterable[Iterable[tuple[str, str]]]) -> dict:
    """Generic inline keyboard from (label, callback_data) pairs."""
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in rows
        ]
    }


def kb_url(rows: Iterable[Iterable[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": label, "url": url} for label, url in row] for row in rows]}


def back(lang: str, target: str = "m:home") -> dict:
    return kb([[(tr(lang, "btn.back"), target)]])


def home(lang: str) -> dict:
    return kb([[(tr(lang, "btn.home"), "m:home")]])


def language_picker() -> dict:
    return kb([[("🇮🇷 فارسی", "lang:fa"), ("🇬🇧 English", "lang:en")]])


def main_menu(lang: str, is_admin: bool = False) -> dict:
    rows: list[list[tuple[str, str]]] = [
        [(tr(lang, "menu.account"), "m:account"), (tr(lang, "menu.configs"), "m:configs")],
        [(tr(lang, "menu.plans"), "m:plans"), (tr(lang, "menu.renew"), "m:renew")],
        [(tr(lang, "menu.usage"), "m:usage"), (tr(lang, "menu.wallet"), "m:wallet")],
        [(tr(lang, "menu.referral"), "m:referral"), (tr(lang, "menu.settings"), "m:settings")],
        [(tr(lang, "menu.support"), "m:support")],
    ]
    if is_admin:
        rows.append([(tr(lang, "menu.admin"), "a:home")])
    return kb(rows)


def settings_menu(lang: str, prefs: dict) -> dict:
    def flag(key: str) -> str:
        return "🟢" if prefs.get(key, True) else "⚪️"

    return kb(
        [
            [(tr(lang, "settings.language"), "set:lang")],
            [(f"{flag('notify_expiry')} {tr(lang, 'settings.notifExpiry')}", "notif:notify_expiry")],
            [(f"{flag('notify_quota')} {tr(lang, 'settings.notifQuota')}", "notif:notify_quota")],
            [(f"{flag('notify_news')} {tr(lang, 'settings.notifNews')}", "notif:notify_news")],
            [(tr(lang, "settings.myId"), "set:id")],
            [(tr(lang, "btn.home"), "m:home")],
        ]
    )


def plan_keyboard(lang: str, plans: Sequence[Any], action: str = "buy:plan", back_to: str = "m:home") -> dict:
    rows = [
        [(f"{p.name} · {p.price:g} {p.currency} · {p.duration_days}d", f"{action}:{p.id}")]
        for p in plans[:12]
    ]
    rows.append([(tr(lang, "btn.back"), back_to)])
    return kb(rows)


def method_keyboard(lang: str, plan_id: int, *, allow_balance: bool, back_to: str = "m:plans") -> dict:
    rows: list[list[tuple[str, str]]] = []
    if allow_balance:
        rows.append([(tr(lang, "plans.payBalance"), f"buy:bal:{plan_id}")])
    rows.append([(tr(lang, "plans.payCard"), f"buy:card:{plan_id}")])
    rows.append([(tr(lang, "plans.payCrypto"), f"buy:crypto:{plan_id}")])
    rows.append([(tr(lang, "btn.back"), back_to)])
    return kb(rows)


def services_keyboard(lang: str, services: Sequence[Any], action: str = "cfg", back_to: str = "m:home") -> dict:
    from app.bot.i18n import status_icon

    rows = []
    for service in services[:20]:
        icon = status_icon(service.status.value)
        rows.append([(f"{icon} {service.label} · #{service.id}", f"{action}:{service.id}")])
    rows.append([(tr(lang, "btn.back"), back_to)])
    return kb(rows)


def config_keyboard(lang: str, service_id: int, *, usable: bool) -> dict:
    rows: list[list[tuple[str, str]]] = []
    if usable:
        rows.append([(tr(lang, "configs.getLink"), f"cfg:link:{service_id}")])
        rows.append([
            (tr(lang, "configs.sub"), f"cfg:sub:{service_id}"),
            (tr(lang, "configs.qr"), f"cfg:qr:{service_id}"),
        ])
    rows.append([(tr(lang, "configs.renew"), f"cfg:ren:{service_id}")])
    rows.append([
        (tr(lang, "configs.rename"), f"cfg:name:{service_id}"),
        (tr(lang, "configs.delete"), f"cfg:del:{service_id}"),
    ])
    rows.append([(tr(lang, "btn.back"), "m:configs")])
    return kb(rows)


def confirm_keyboard(lang: str, yes_data: str, no_data: str) -> dict:
    return kb([[(tr(lang, "common.yes"), yes_data), (tr(lang, "common.no"), no_data)]])


def admin_menu(lang: str) -> dict:
    return kb(
        [
            [(tr(lang, "admin.stats"), "a:stats"), (tr(lang, "admin.users"), "a:users")],
            [(tr(lang, "admin.payments"), "a:pay"), (tr(lang, "admin.nodes"), "a:nodes")],
            [(tr(lang, "admin.report"), "a:report"), (tr(lang, "admin.alerts"), "a:alerts")],
            [(tr(lang, "admin.plans"), "a:plans"), (tr(lang, "admin.find"), "a:find")],
            [(tr(lang, "admin.broadcast"), "a:bc"), (tr(lang, "admin.backup"), "a:backup")],
            [(tr(lang, "btn.home"), "m:home")],
        ]
    )


def admin_user_keyboard(lang: str, user_id: int) -> dict:
    return kb(
        [
            [("➕ 10", f"a:bal:{user_id}:10"), ("➕ 25", f"a:bal:{user_id}:25"), ("➕ 50", f"a:bal:{user_id}:50")],
            [(tr(lang, "configs.renew"), f"a:renew:{user_id}")],
            [(tr(lang, "common.disable"), f"a:toggle:{user_id}")],
            [(tr(lang, "btn.back"), "a:users")],
        ]
    )


def node_keyboard(lang: str, node_id: int) -> dict:
    return kb(
        [
            [("❤️", f"a:nhealth:{node_id}"), ("🔄 Sync", f"a:nsync:{node_id}")],
            [(tr(lang, "btn.back"), "a:nodes")],
        ]
    )


def payment_keyboard(lang: str, payment_id: int) -> dict:
    return kb([[(tr(lang, "common.yes"), f"pay:ok:{payment_id}"), (tr(lang, "common.no"), f"pay:no:{payment_id}")]])


def alert_keyboard(lang: str, alert_id: int) -> dict:
    return kb([[("✅", f"a:ack:{alert_id}")]])


def plan_toggle_keyboard(lang: str, plans: Sequence[Any]) -> dict:
    rows = [
        [(f"{'🟢' if p.is_active else '⚪️'} {p.name} · {p.price:g} {p.currency}", f"a:ptoggle:{p.id}")]
        for p in plans[:12]
    ]
    rows.append([(tr(lang, "btn.back"), "a:home")])
    return kb(rows)


def broadcast_keyboard(lang: str) -> dict:
    return kb(
        [
            [(tr(lang, "admin.broadcastAll"), "a:bcall")],
            [(tr(lang, "admin.broadcastActive"), "a:bcactive")],
            [(tr(lang, "btn.back"), "a:home")],
        ]
    )
