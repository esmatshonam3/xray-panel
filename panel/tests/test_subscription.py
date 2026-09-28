"""Public subscription surface: capability URL, formats, guards."""
from __future__ import annotations

import base64
from datetime import timedelta

from app.db.base import utcnow
from app.db.models import Service, ServiceStatus


def _provision(client, auth_headers, user, inbound, plan) -> dict:
    response = client.post(
        "/api/v1/services",
        headers=auth_headers,
        json={"user_id": user.id, "plan_id": plan.id, "inbound_id": inbound.id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_subscription_returns_base64_links(client, auth_headers, normal_user, inbound, plan):
    service = _provision(client, auth_headers, normal_user, inbound, plan)
    token = service["subscription_url"].rsplit("/", 1)[-1]

    response = client.get(f"/api/v1/subscription/{token}")
    assert response.status_code == 200
    decoded = base64.b64decode(response.text + "=" * (-len(response.text) % 4)).decode()
    assert "vless://" in decoded
    # v2rayN reads these headers for the usage bar.
    assert "upload=" in response.headers["Subscription-Userinfo"]
    assert "download=" in response.headers["Subscription-Userinfo"]


def test_subscription_plain_format(client, auth_headers, normal_user, inbound, plan):
    service = _provision(client, auth_headers, normal_user, inbound, plan)
    token = service["subscription_url"].rsplit("/", 1)[-1]
    response = client.get(f"/api/v1/subscription/{token}?format=plain")
    assert response.status_code == 200
    assert response.text.startswith("vless://")


def test_subscription_clash_format(client, auth_headers, normal_user, inbound, plan):
    service = _provision(client, auth_headers, normal_user, inbound, plan)
    token = service["subscription_url"].rsplit("/", 1)[-1]
    response = client.get(f"/api/v1/subscription/{token}/clash.yaml")
    assert response.status_code == 200
    assert "proxies:" in response.text
    assert response.headers["content-type"].startswith("text/yaml")


def test_subscription_qr(client, auth_headers, normal_user, inbound, plan):
    service = _provision(client, auth_headers, normal_user, inbound, plan)
    token = service["subscription_url"].rsplit("/", 1)[-1]
    response = client.get(f"/api/v1/subscription/{token}/qr.png")
    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG")


def test_subscription_info_payload(client, auth_headers, normal_user, inbound, plan):
    service = _provision(client, auth_headers, normal_user, inbound, plan)
    token = service["subscription_url"].rsplit("/", 1)[-1]
    body = client.get(f"/api/v1/subscription/{token}/info").json()
    assert body["status"] == "active"
    assert body["usable"] is True
    assert body["limit_human"].endswith("GB")


def test_expired_subscription_is_blocked(client, auth_headers, normal_user, inbound, plan, db):
    service = _provision(client, auth_headers, normal_user, inbound, plan)
    token = service["subscription_url"].rsplit("/", 1)[-1]

    row = db.get(Service, service["id"])
    row.status = ServiceStatus.expired
    row.expires_at = utcnow() - timedelta(days=1)
    db.commit()

    response = client.get(f"/api/v1/subscription/{token}")
    assert response.status_code == 403
    assert "expired" in response.json()["detail"].lower()


def test_quota_exhausted_subscription_is_blocked(client, auth_headers, normal_user, inbound, plan, db):
    service = _provision(client, auth_headers, normal_user, inbound, plan)
    token = service["subscription_url"].rsplit("/", 1)[-1]

    row = db.get(Service, service["id"])
    row.used_down_bytes = row.traffic_limit_bytes + 1
    db.commit()

    response = client.get(f"/api/v1/subscription/{token}")
    assert response.status_code == 403
    assert "quota" in response.json()["detail"].lower()


def test_unknown_token_returns_404(client):
    assert client.get("/api/v1/subscription/does-not-exist").status_code == 404


def test_deleted_service_subscription_is_gone(client, auth_headers, normal_user, inbound, plan, db):
    service = _provision(client, auth_headers, normal_user, inbound, plan)
    token = service["subscription_url"].rsplit("/", 1)[-1]

    row = db.get(Service, service["id"])
    row.status = ServiceStatus.deleted
    db.commit()

    assert client.get(f"/api/v1/subscription/{token}").status_code == 404


def test_subscription_aggregates_sibling_services(client, auth_headers, normal_user, inbound, plan):
    first = _provision(client, auth_headers, normal_user, inbound, plan)
    _provision(client, auth_headers, normal_user, inbound, plan)
    token = first["subscription_url"].rsplit("/", 1)[-1]

    body = client.get(f"/api/v1/subscription/{token}?format=plain").text
    assert len([line for line in body.splitlines() if line.startswith("vless://")]) == 2
