"""Telegram bot: navigation, i18n, authorization and the purchase flow.

The bot client is replaced with a recorder so no network call is made and every
outgoing message/keyboard can be asserted on.
"""
from __future__ import annotations

import uuid as uuid_lib
from datetime import timedelta

import pytest

from app.bot import i18n as bot_i18n
from app.bot import router as bot_router
from app.db.base import utcnow
from app.db.models import (
    BotUser,
    Payment,
    PaymentStatus,
    Plan,
    Role,
    Service,
    ServiceStatus,
    User,
    UserStatus,
)


# --------------------------------------------------------------------------- #
#  Test double
# --------------------------------------------------------------------------- #
class RecordingClient:
    """Drop-in replacement for TelegramClient that records every call."""

    configured = True

    def __init__(self):
        self.log: list[dict] = []
        self.messages: list[dict] = []
        self.photos: list[dict] = []
        self.edits: list[dict] = []
        self.answers: list[dict] = []

    def send_message(self, chat_id, text, **kwargs):
        entry = {"kind": "message", "chat_id": chat_id, "text": text, **kwargs}
        self.messages.append(entry)
        self.log.append(entry)
        return {"message_id": len(self.messages)}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        entry = {"kind": "edit", "chat_id": chat_id, "message_id": message_id, "text": text, **kwargs}
        self.edits.append(entry)
        self.log.append(entry)
        return {"message_id": message_id}

    def edit_reply_markup(self, *args, **kwargs):
        return {"ok": True}

    def delete_message(self, *args, **kwargs):
        return {"ok": True}

    def send_photo(self, chat_id, photo, **kwargs):
        entry = {"kind": "photo", "chat_id": chat_id, "photo": photo, **kwargs}
        self.photos.append(entry)
        self.log.append(entry)
        return {"message_id": len(self.photos)}

    def send_chat_action(self, *args, **kwargs):
        return {"ok": True}

    def answer_callback(self, callback_id, text="", alert=False):
        self.answers.append({"id": callback_id, "text": text, "alert": alert})
        return {"ok": True}

    # -- helpers -----------------------------------------------------------
    @property
    def last_text(self) -> str:
        """Newest outgoing text, whichever API produced it."""
        for entry in reversed(self.log):
            if "text" in entry:
                return entry["text"]
        return ""

    @property
    def last_keyboard(self) -> dict:
        for entry in reversed(self.log):
            if "text" in entry:
                return entry.get("keyboard") or {}
        return {}

    def callback_data(self) -> list[str]:
        rows = self.last_keyboard.get("inline_keyboard", [])
        return [btn["callback_data"] for row in rows for btn in row if "callback_data" in btn]

    def reset(self):
        self.log.clear()
        self.messages.clear()
        self.photos.clear()
        self.edits.clear()
        self.answers.clear()


@pytest.fixture
def bot(monkeypatch) -> RecordingClient:
    recorder = RecordingClient()
    monkeypatch.setattr(bot_router, "client", recorder)
    bot_router._bucket._tokens.clear()
    bot_router._bucket._last.clear()
    return recorder


TG_ID = 555000111


def message(text: str, *, tg_id: int = TG_ID, username: str = "tester", photo: bool = False) -> dict:
    payload: dict = {
        "message_id": 1,
        "chat": {"id": tg_id},
        "from": {"id": tg_id, "username": username, "language_code": "fa"},
    }
    if photo:
        payload["photo"] = [{"file_id": "receipt-file-id"}]
    else:
        payload["text"] = text
    return payload


def callback(data: str, *, tg_id: int = TG_ID, message_id: int = 10) -> dict:
    return {
        "id": f"cb-{data}",
        "data": data,
        "message": {"message_id": message_id, "chat": {"id": tg_id}},
        "from": {"id": tg_id, "username": "tester", "language_code": "fa"},
    }


# --------------------------------------------------------------------------- #
#  i18n
# --------------------------------------------------------------------------- #
def test_bot_dictionaries_are_in_sync():
    fa, en = set(bot_i18n.FA), set(bot_i18n.EN)
    assert fa - en == set(), f"missing English keys: {sorted(fa - en)}"
    assert en - fa == set(), f"missing Persian keys: {sorted(en - fa)}"
    assert len(fa) > 100


def test_tr_interpolates_and_falls_back():
    assert "Ali" in bot_i18n.tr("en", "configs.detail", label="Ali", id=7)
    # unknown language falls back to English
    assert bot_i18n.tr("de", "btn.home") == bot_i18n.EN["btn.home"]
    # unknown key returns the key itself rather than raising
    assert bot_i18n.tr("en", "nope.nope") == "nope.nope"


def test_normalize_maps_locales():
    assert bot_i18n.normalize("fa-IR") == "fa"
    assert bot_i18n.normalize("en-GB") == "en"
    assert bot_i18n.normalize("ar") == "fa"
    assert bot_i18n.normalize(None) == "fa"
    assert bot_i18n.normalize("de") == "en"


# --------------------------------------------------------------------------- #
#  Onboarding
# --------------------------------------------------------------------------- #
def test_start_asks_for_language_on_first_contact(db, bot):
    bot_router.handle_update(db, {"message": message("/start")})

    assert bot.messages, "the bot answered nothing"
    assert "🇮🇷" in str(bot.last_keyboard)
    assert "lang:fa" in bot.callback_data()
    assert "lang:en" in bot.callback_data()


def test_language_choice_switches_direction_and_persists(db, bot):
    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:en")})

    row = db.query(BotUser).filter(BotUser.telegram_id == TG_ID).one()
    assert row.language == "en"
    assert "Main menu" in bot.last_text


def test_second_start_shows_main_menu(db, bot):
    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"message": message("/start")})

    data = bot.callback_data()
    assert "m:account" in data and "m:configs" in data and "m:plans" in data
    # a plain customer must not see the admin button
    assert "a:home" not in data


def test_telegram_account_is_created_and_linked(db, bot):
    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})

    row = db.query(BotUser).filter(BotUser.telegram_id == TG_ID).one()
    assert row.user_id is not None
    user = db.get(User, row.user_id)
    assert user.telegram_id == TG_ID
    assert user.role == Role.user


def test_referral_deep_link_is_applied(db, bot, db_user_factory):
    referrer = db_user_factory(username="referrer")
    from app.services.billing import ensure_referral_code

    code = ensure_referral_code(db, referrer)

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot_router.handle_update(db, {"message": message(f"/start {code}")})

    row = db.query(BotUser).filter(BotUser.telegram_id == TG_ID).one()
    assert db.get(User, row.user_id).referred_by_id == referrer.id


# --------------------------------------------------------------------------- #
#  Authorization
# --------------------------------------------------------------------------- #
def test_admin_console_is_denied_for_regular_users(db, bot):
    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"message": message("/admin")})
    assert "⛔" in bot.last_text

    bot.reset()
    bot_router.handle_update(db, {"callback_query": callback("a:stats")})
    assert bot.answers[-1]["alert"] is True
    assert "⛔" in bot.answers[-1]["text"]


def test_admin_sees_the_console(db, bot, db_user_factory):
    admin = db_user_factory(username="boss", role=Role.owner, telegram_id=TG_ID)
    assert admin.is_staff

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"message": message("/admin")})
    data = bot.callback_data()
    assert "a:stats" in data and "a:pay" in data and "a:nodes" in data


def test_cannot_open_someone_elses_config(db, bot, db_user_factory, make_service):
    owner = db_user_factory(username="owner")
    other = db_user_factory(username="other")
    service = make_service(owner)

    # link the telegram identity to `other`
    other.telegram_id = TG_ID
    db.commit()

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"callback_query": callback(f"cfg:{service.id}")})
    assert "⛔" in bot.last_text


# --------------------------------------------------------------------------- #
#  Config delivery
# --------------------------------------------------------------------------- #
def test_config_flow_delivers_link_and_qr(db, bot, db_user_factory, make_service):
    user = db_user_factory(username="customer", telegram_id=TG_ID)
    service = make_service(user)

    bot_router.handle_update(db, {"message": message("/configs")})
    assert f"cfg:{service.id}" in bot.callback_data()

    bot.reset()
    bot_router.handle_update(db, {"callback_query": callback(f"cfg:{service.id}")})
    assert "active" in bot.last_text.lower() or "فعال" in bot.last_text
    assert f"cfg:link:{service.id}" in bot.callback_data()

    bot.reset()
    bot_router.handle_update(db, {"callback_query": callback(f"cfg:link:{service.id}")})
    assert "vless://" in bot.last_text

    bot.reset()
    bot_router.handle_update(db, {"callback_query": callback(f"cfg:qr:{service.id}")})
    assert bot.photos and isinstance(bot.photos[-1]["photo"], bytes)
    assert bot.photos[-1]["photo"].startswith(b"\x89PNG")


def test_expired_config_refuses_to_hand_out_a_link(db, bot, db_user_factory, make_service):
    user = db_user_factory(username="customer", telegram_id=TG_ID)
    service = make_service(user)
    service.status = ServiceStatus.expired
    service.expires_at = utcnow() - timedelta(days=1)
    db.commit()

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"callback_query": callback(f"cfg:link:{service.id}")})
    assert "⛔" in bot.last_text


def test_rename_flow(db, bot, db_user_factory, make_service):
    user = db_user_factory(username="customer", telegram_id=TG_ID)
    service = make_service(user)

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"callback_query": callback(f"cfg:name:{service.id}")})
    assert "✏️" in bot.last_text

    bot_router.handle_update(db, {"message": message("My fast line")})
    db.refresh(service)
    assert service.label == "My fast line"


# --------------------------------------------------------------------------- #
#  Purchase flow
# --------------------------------------------------------------------------- #
def test_purchase_with_insufficient_balance_is_refused(db, bot, db_user_factory, plan):
    db_user_factory(username="poor", telegram_id=TG_ID, balance=0)

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"callback_query": callback(f"buy:plan:{plan.id}")})
    # the wallet button must not even be offered
    assert f"buy:bal:{plan.id}" not in bot.callback_data()

    bot.reset()
    bot_router.handle_update(db, {"callback_query": callback(f"buy:bal:{plan.id}")})
    assert "⛔" in bot.last_text


def test_purchase_with_balance_provisions_immediately(db, bot, db_user_factory, plan, inbound):
    user = db_user_factory(username="rich", telegram_id=TG_ID, balance=100)

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"callback_query": callback(f"buy:bal:{plan.id}")})

    db.refresh(user)
    assert user.balance == pytest.approx(100 - plan.price)
    services = db.query(Service).filter(Service.user_id == user.id).all()
    assert len(services) == 1
    assert services[0].status == ServiceStatus.active


def test_manual_purchase_creates_order_and_notifies_admins(db, bot, db_user_factory, plan):
    user = db_user_factory(username="payer", telegram_id=TG_ID, balance=0)

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"callback_query": callback(f"buy:card:{plan.id}")})

    payment = db.query(Payment).filter(Payment.user_id == user.id).one()
    assert payment.status == PaymentStatus.pending
    assert payment.reference in bot.last_text
    assert "X" in bot.last_text  # payment instructions block rendered


def test_receipt_photo_marks_order_for_review(db, bot, db_user_factory, plan):
    user = db_user_factory(username="payer", telegram_id=TG_ID, balance=0)

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot_router.handle_update(db, {"callback_query": callback(f"buy:card:{plan.id}")})
    bot.reset()

    bot_router.handle_update(db, {"message": message("", photo=True)})

    payment = db.query(Payment).filter(Payment.user_id == user.id).one()
    assert payment.status == PaymentStatus.awaiting_review
    assert payment.receipt_file_id == "receipt-file-id"
    assert "🧾" in bot.last_text


def test_admin_approval_activates_the_service(db, bot, db_user_factory, plan, inbound):
    admin = db_user_factory(username="boss", role=Role.owner, telegram_id=999)
    user = db_user_factory(username="payer", telegram_id=TG_ID, balance=0)

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot_router.handle_update(db, {"callback_query": callback(f"buy:card:{plan.id}")})
    payment = db.query(Payment).filter(Payment.user_id == user.id).one()

    bot_router.handle_update(db, {"callback_query": callback(f"pay:ok:{payment.id}", tg_id=999)})

    db.refresh(payment)
    assert payment.status == PaymentStatus.paid
    assert db.query(Service).filter(Service.user_id == user.id).count() == 1


def test_admin_cannot_approve_twice(db, bot, db_user_factory, plan, inbound):
    db_user_factory(username="boss", role=Role.owner, telegram_id=999)
    user = db_user_factory(username="payer", telegram_id=TG_ID, balance=0)

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot_router.handle_update(db, {"callback_query": callback(f"buy:card:{plan.id}")})
    payment = db.query(Payment).filter(Payment.user_id == user.id).one()

    bot_router.handle_update(db, {"callback_query": callback(f"pay:ok:{payment.id}", tg_id=999)})
    bot_router.handle_update(db, {"callback_query": callback(f"pay:ok:{payment.id}", tg_id=999)})

    assert db.query(Service).filter(Service.user_id == user.id).count() == 1
    assert bot.answers[-1]["alert"] is True


# --------------------------------------------------------------------------- #
#  Settings & wallet
# --------------------------------------------------------------------------- #
def test_notification_toggles_persist(db, bot):
    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"callback_query": callback("notif:notify_quota")})

    row = db.query(BotUser).filter(BotUser.telegram_id == TG_ID).one()
    assert (row.state_data or {}).get("prefs", {}).get("notify_quota") is False


def test_wallet_topup_creates_a_payment(db, bot, db_user_factory):
    user = db_user_factory(username="customer", telegram_id=TG_ID, balance=0)

    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()

    bot_router.handle_update(db, {"callback_query": callback("m:topup")})
    bot_router.handle_update(db, {"message": message("25")})

    payment = db.query(Payment).filter(Payment.user_id == user.id).one()
    assert payment.purpose == "topup"
    assert payment.amount == 25


def test_usage_screen_renders(db, bot, db_user_factory, make_service):
    user = db_user_factory(username="customer", telegram_id=TG_ID)
    make_service(user)

    bot_router.handle_update(db, {"message": message("/usage")})
    # unknown command falls back to the menu
    assert bot.messages

    bot.reset()
    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    bot.reset()
    bot_router.handle_update(db, {"callback_query": callback("m:usage")})
    assert "▰" in bot.last_text or "▱" in bot.last_text


# --------------------------------------------------------------------------- #
#  Robustness
# --------------------------------------------------------------------------- #
def test_malformed_callback_does_not_crash(db, bot):
    bot_router.handle_update(db, {"message": message("/start")})
    bot_router.handle_update(db, {"callback_query": callback("lang:fa")})
    for payload in ("cfg:", "cfg:link:", "buy:", "a:", "pay:", "rn:", "notif:"):
        bot_router.handle_update(db, {"callback_query": callback(payload)})


def test_burst_is_rate_limited(db, bot):
    for _ in range(12):
        bot_router.handle_update(db, {"message": message("/start")})
    assert any("⏳" in m["text"] for m in bot.messages)


def test_every_callback_is_answered(db, bot, db_user_factory, make_service):
    """Telegram shows a spinner until answerCallbackQuery is called."""
    user = db_user_factory(username="customer", telegram_id=TG_ID)
    service = make_service(user)

    for data in ("m:home", "m:account", "m:configs", f"cfg:{service.id}", "m:settings", "m:wallet"):
        before = len(bot.answers)
        bot_router.handle_update(db, {"callback_query": callback(data)})
        assert len(bot.answers) > before, f"callback {data} was never answered"
