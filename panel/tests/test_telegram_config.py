"""Telegram bot credentials managed from the panel.

Covers the runtime-configuration layer that lets an operator paste a token and
admin chat IDs into the UI instead of editing environment variables:

  * precedence: database > environment > default
  * secrets are encrypted at rest and never returned by the API
  * every consumer (client, alerts, bot RBAC, webhook) resolves at call time
"""
from __future__ import annotations

import pytest

from app.core.security import decrypt
from app.db.models import Setting
from app.services import runtime_config

GOOD_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
ADMIN_ID = 424242


@pytest.fixture(autouse=True)
def _fresh_cache():
    """The runtime cache is process-global; never let one test leak into another."""
    runtime_config.invalidate()
    yield
    runtime_config.invalidate()


# --------------------------------------------------------------------------- #
#  Access control
# --------------------------------------------------------------------------- #
def test_config_requires_admin(client, user_headers):
    assert client.get("/api/v1/telegram/config", headers=user_headers).status_code == 403
    assert client.put("/api/v1/telegram/config", headers=user_headers, json={}).status_code == 403
    assert client.delete("/api/v1/telegram/config", headers=user_headers).status_code == 403


def test_config_requires_authentication(client):
    assert client.get("/api/v1/telegram/config").status_code == 401


# --------------------------------------------------------------------------- #
#  Saving & precedence
# --------------------------------------------------------------------------- #
def test_defaults_come_from_the_environment(client, auth_headers):
    body = client.get("/api/v1/telegram/config", headers=auth_headers).json()
    assert body["has_token"] is False
    assert body["token_source"] == "environment"
    assert body["enabled"] is False


def test_saving_a_token_takes_precedence_over_the_environment(client, auth_headers, db):
    response = client.put(
        "/api/v1/telegram/config",
        headers=auth_headers,
        json={"bot_token": GOOD_TOKEN, "admin_ids": [ADMIN_ID], "enabled": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["has_token"] is True
    assert body["token_source"] == "database"
    assert body["admin_ids"] == [ADMIN_ID]
    assert body["enabled"] is True

    # The resolver must reflect the change immediately.
    assert runtime_config.bot_token() == GOOD_TOKEN
    assert runtime_config.admin_ids() == [ADMIN_ID]
    assert runtime_config.is_enabled() is True


def test_token_is_encrypted_at_rest_and_never_returned(client, auth_headers, db):
    client.put("/api/v1/telegram/config", headers=auth_headers, json={"bot_token": GOOD_TOKEN})

    row = db.get(Setting, runtime_config.TELEGRAM_TOKEN)
    assert row is not None
    assert row.is_secret is True
    assert GOOD_TOKEN not in str(row.value), "the token was stored in plaintext"
    assert decrypt(row.value) == GOOD_TOKEN

    body = client.get("/api/v1/telegram/config", headers=auth_headers).json()
    serialised = str(body)
    secret_half = GOOD_TOKEN.split(":", 1)[1]
    assert GOOD_TOKEN not in serialised
    assert secret_half not in serialised, "the secret half leaked into the API response"
    assert body["token_hint"]


def test_username_accepts_an_at_prefix(client, auth_headers):
    body = client.put(
        "/api/v1/telegram/config", headers=auth_headers, json={"bot_username": "@my_vpn_bot"}
    ).json()
    assert body["bot_username"] == "my_vpn_bot"


def test_webhook_secret_is_generated_when_empty(client, auth_headers, db):
    body = client.put("/api/v1/telegram/config", headers=auth_headers, json={"webhook_secret": ""}).json()
    assert body["webhook_secret_set"] is True
    assert body["webhook_url"].endswith(f"/api/v1/telegram/webhook/{runtime_config.webhook_secret(db)}")
    assert len(runtime_config.webhook_secret(db)) == 48


def test_clear_token_falls_back_to_the_environment(client, auth_headers, db):
    client.put("/api/v1/telegram/config", headers=auth_headers, json={"bot_token": GOOD_TOKEN})
    assert runtime_config.bot_token() == GOOD_TOKEN

    body = client.put("/api/v1/telegram/config", headers=auth_headers, json={"clear_token": True}).json()
    assert body["has_token"] is False
    assert body["token_source"] == "environment"
    assert db.get(Setting, runtime_config.TELEGRAM_TOKEN) is None


def test_every_editable_field_round_trips(client, auth_headers, db):
    """Regression guard: a typo in a config key name used to 500 the endpoint."""
    saved = client.put(
        "/api/v1/telegram/config",
        headers=auth_headers,
        json={
            "bot_token": GOOD_TOKEN,
            "bot_username": "my_vpn_bot",
            "webhook_secret": "a-strong-secret",
            "admin_ids": [ADMIN_ID],
            "enabled": True,
            "auto_set_webhook": True,
        },
    )
    assert saved.status_code == 200, saved.text

    body = client.get("/api/v1/telegram/config", headers=auth_headers).json()
    assert body["has_token"] is True
    assert body["bot_username"] == "my_vpn_bot"
    assert body["webhook_secret_set"] is True
    assert body["admin_ids"] == [ADMIN_ID]
    assert body["enabled"] is True
    assert body["auto_set_webhook"] is True

    # Every field must also be persisted under its declared key.
    for key in runtime_config.TELEGRAM_KEYS:
        assert db.get(Setting, key) is not None, f"{key} was not persisted"

    config = runtime_config.telegram_config(db, fresh=True)
    assert config["auto_set_webhook"] is True
    assert config["enabled"] is True


def test_toggles_can_be_switched_back_off(client, auth_headers):
    client.put(
        "/api/v1/telegram/config",
        headers=auth_headers,
        json={"bot_token": GOOD_TOKEN, "enabled": True, "auto_set_webhook": True},
    )
    body = client.put(
        "/api/v1/telegram/config", headers=auth_headers, json={"enabled": False, "auto_set_webhook": False}
    ).json()
    assert body["enabled"] is False
    assert body["auto_set_webhook"] is False
    # The token itself is untouched by a toggle update.
    assert body["has_token"] is True


def test_delete_removes_every_override(client, auth_headers, db):
    client.put(
        "/api/v1/telegram/config",
        headers=auth_headers,
        json={"bot_token": GOOD_TOKEN, "admin_ids": [ADMIN_ID], "enabled": True},
    )
    response = client.delete("/api/v1/telegram/config", headers=auth_headers)
    assert response.status_code == 200

    for key in runtime_config.TELEGRAM_KEYS:
        assert db.get(Setting, key) is None
    assert runtime_config.bot_token() == ""
    assert runtime_config.is_enabled() is False


def test_generate_secret_endpoint(client, auth_headers):
    first = client.post("/api/v1/telegram/generate-secret", headers=auth_headers).json()["webhook_secret"]
    second = client.post("/api/v1/telegram/generate-secret", headers=auth_headers).json()["webhook_secret"]
    assert len(first) == 48 and first != second


# --------------------------------------------------------------------------- #
#  Validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_token",
    ["not-a-token", "abcdef:123", "123456789", "123456789:", "123456789:short"],
)
def test_malformed_token_is_rejected(client, auth_headers, bad_token):
    response = client.put("/api/v1/telegram/config", headers=auth_headers, json={"bot_token": bad_token})
    assert response.status_code == 422


def test_empty_token_means_leave_it_alone(client, auth_headers):
    client.put("/api/v1/telegram/config", headers=auth_headers, json={"bot_token": GOOD_TOKEN})
    body = client.put("/api/v1/telegram/config", headers=auth_headers, json={"bot_token": ""}).json()
    assert body["has_token"] is True
    assert runtime_config.bot_token() == GOOD_TOKEN


@pytest.mark.parametrize("bad_ids", [["abc"], [-5], [0], ["12x"]])
def test_malformed_admin_ids_are_rejected(client, auth_headers, bad_ids):
    response = client.put("/api/v1/telegram/config", headers=auth_headers, json={"admin_ids": bad_ids})
    assert response.status_code == 422


def test_admin_ids_are_deduplicated_and_sorted(client, auth_headers):
    body = client.put(
        "/api/v1/telegram/config", headers=auth_headers, json={"admin_ids": [900, 100, 900]}
    ).json()
    assert body["admin_ids"] == [100, 900]


def test_validate_without_a_token_reports_an_error(client, auth_headers):
    body = client.post("/api/v1/telegram/validate", headers=auth_headers).json()
    assert body["ok"] is False
    assert "No token" in body["error"]


def test_validate_reports_telegram_rejection(client, auth_headers, monkeypatch):
    """Telegram returns nothing for a bad token, so the endpoint must say so."""
    monkeypatch.setattr("app.bot.telegram.TelegramClient.get_me", lambda self: None)
    body = client.post(
        f"/api/v1/telegram/validate?token={GOOD_TOKEN}", headers=auth_headers
    ).json()
    assert body["ok"] is False
    assert "rejected" in body["error"]


def test_validate_adopts_the_reported_username(client, auth_headers, db, monkeypatch):
    monkeypatch.setattr(
        "app.bot.telegram.TelegramClient.get_me",
        lambda self: {"id": 1, "username": "adopted_bot", "first_name": "Bot"},
    )
    body = client.post(f"/api/v1/telegram/validate?token={GOOD_TOKEN}", headers=auth_headers).json()
    assert body["ok"] is True
    assert body["bot"]["username"] == "adopted_bot"
    assert runtime_config.telegram_config(db, fresh=True)["username"] == "adopted_bot"


def test_test_message_requires_admin_ids(client, auth_headers):
    client.put("/api/v1/telegram/config", headers=auth_headers, json={"bot_token": GOOD_TOKEN})
    body = client.post("/api/v1/telegram/test", headers=auth_headers).json()
    assert body["ok"] is False
    assert "admin" in body["error"].lower()


def test_test_message_delivers_to_every_admin(client, auth_headers, monkeypatch):
    sent: list[int] = []

    def fake_send(self, chat_id, text, **kwargs):
        sent.append(chat_id)
        return {"message_id": 1}

    monkeypatch.setattr("app.bot.telegram.TelegramClient.send_message", fake_send)
    client.put(
        "/api/v1/telegram/config",
        headers=auth_headers,
        json={"bot_token": GOOD_TOKEN, "admin_ids": [111, 222]},
    )
    body = client.post("/api/v1/telegram/test", headers=auth_headers).json()

    assert body["ok"] is True
    assert body["delivered"] == 2
    assert sorted(sent) == [111, 222]


# --------------------------------------------------------------------------- #
#  Consumers resolve at call time
# --------------------------------------------------------------------------- #
def test_telegram_client_uses_the_panel_token(client, auth_headers):
    from app.bot.telegram import TelegramClient

    assert TelegramClient().token == ""  # nothing configured yet
    client.put("/api/v1/telegram/config", headers=auth_headers, json={"bot_token": GOOD_TOKEN})
    assert TelegramClient().token == GOOD_TOKEN


def test_alerts_use_the_panel_admin_ids(client, auth_headers):
    from app.services.alerts import _admin_chat_ids

    client.put("/api/v1/telegram/config", headers=auth_headers, json={"admin_ids": [ADMIN_ID]})
    assert _admin_chat_ids() == [ADMIN_ID]


def test_bot_rbac_uses_the_panel_admin_ids(client, auth_headers, db, db_user_factory):
    from app.bot.router import _is_admin

    user = db_user_factory(username="operator", telegram_id=ADMIN_ID)
    bot_user = db.query(__import__("app.db.models", fromlist=["BotUser"]).BotUser).first()
    if bot_user is None:
        from app.db.models import BotUser

        bot_user = BotUser(telegram_id=ADMIN_ID, user_id=user.id)
        db.add(bot_user)
        db.commit()

    # Not an admin yet.
    client.delete("/api/v1/telegram/config", headers=auth_headers)
    db.refresh(bot_user)
    assert _is_admin(bot_user) is False

    client.put("/api/v1/telegram/config", headers=auth_headers, json={"admin_ids": [ADMIN_ID]})
    db.refresh(bot_user)
    assert _is_admin(bot_user) is True


# --------------------------------------------------------------------------- #
#  Webhook ingress
# --------------------------------------------------------------------------- #
def _enable_bot(client, auth_headers, secret: str = "s3cret-value"):
    client.put(
        "/api/v1/telegram/config",
        headers=auth_headers,
        json={"bot_token": GOOD_TOKEN, "enabled": True, "webhook_secret": secret},
    )
    return secret


def test_webhook_is_unavailable_until_the_bot_is_configured(client):
    response = client.post(
        "/api/v1/telegram/webhook/webhook",
        json={"message": {"message_id": 1, "chat": {"id": 1}, "from": {"id": 1}, "text": "/start"}},
    )
    assert response.status_code == 503


def test_webhook_rejects_a_wrong_secret(client, auth_headers):
    _enable_bot(client, auth_headers)
    response = client.post(
        "/api/v1/telegram/webhook/not-the-secret",
        json={"message": {"message_id": 1, "chat": {"id": 1}, "from": {"id": 1}, "text": "/start"}},
    )
    assert response.status_code == 403


def test_webhook_accepts_the_configured_secret(client, auth_headers, monkeypatch):
    secret = _enable_bot(client, auth_headers)

    handled: list[dict] = []
    monkeypatch.setattr("app.bot.router.handle_update", lambda db, update: handled.append(update))

    response = client.post(
        f"/api/v1/telegram/webhook/{secret}",
        json={"message": {"message_id": 1, "chat": {"id": 5}, "from": {"id": 5}, "text": "/start"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(handled) == 1


def test_webhook_follows_a_secret_rotation(client, auth_headers, monkeypatch):
    _enable_bot(client, auth_headers, secret="first-secret")
    monkeypatch.setattr("app.bot.router.handle_update", lambda db, update: None)

    payload = {"message": {"message_id": 1, "chat": {"id": 5}, "from": {"id": 5}, "text": "/start"}}
    assert client.post("/api/v1/telegram/webhook/first-secret", json=payload).status_code == 200

    client.put("/api/v1/telegram/config", headers=auth_headers, json={"webhook_secret": "second-secret"})
    assert client.post("/api/v1/telegram/webhook/first-secret", json=payload).status_code == 403
    assert client.post("/api/v1/telegram/webhook/second-secret", json=payload).status_code == 200


# --------------------------------------------------------------------------- #
#  Status surface
# --------------------------------------------------------------------------- #
def test_status_reports_configuration_state(client, auth_headers, monkeypatch):
    monkeypatch.setattr("app.bot.telegram.TelegramClient.get_me", lambda self: {"username": "bot"})
    monkeypatch.setattr(
        "app.bot.telegram.TelegramClient.get_webhook_info", lambda self: {"url": "https://x/hook"}
    )

    before = client.get("/api/v1/telegram/status", headers=auth_headers).json()
    assert before["configured"] is False

    client.put(
        "/api/v1/telegram/config",
        headers=auth_headers,
        json={"bot_token": GOOD_TOKEN, "enabled": True, "admin_ids": [ADMIN_ID]},
    )
    after = client.get("/api/v1/telegram/status", headers=auth_headers).json()
    assert after["configured"] is True
    assert after["enabled"] is True
    assert after["admin_ids"] == [ADMIN_ID]
    assert after["token_source"] == "database"


def test_system_info_exposes_telegram_state(client, auth_headers):
    body = client.get("/api/v1/system/info", headers=auth_headers).json()
    assert body["integrations"]["telegram_configured"] is False
    assert body["integrations"]["telegram_source"] == "environment"

    client.put("/api/v1/telegram/config", headers=auth_headers, json={"bot_token": GOOD_TOKEN})
    body = client.get("/api/v1/system/info", headers=auth_headers).json()
    assert body["integrations"]["telegram_configured"] is True
    assert body["integrations"]["telegram_source"] == "database"
