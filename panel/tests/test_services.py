"""Service lifecycle: provisioning, quota, expiry, renewal, sync."""
from __future__ import annotations

from datetime import timedelta

from app.db.base import utcnow
from app.db.models import Service, ServiceStatus, User
from app.db.models import NodeStatus, Security, Transport
from app.services.provisioning import (
    collect_node_stats,
    enforce_quota,
    expire_services,
    push_service,
    sync_node,
)


def _create_service(client, auth_headers, user, inbound, plan) -> dict:
    response = client.post(
        "/api/v1/services",
        headers=auth_headers,
        json={
            "user_id": user.id,
            "plan_id": plan.id,
            "inbound_id": inbound.id,
            "label": "my-config",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_service_returns_links(client, auth_headers, normal_user, inbound, plan, mock_node_agent):
    body = _create_service(client, auth_headers, normal_user, inbound, plan)
    assert body["status"] == "active"
    assert body["protocol"] == "vless"
    assert body["raw_link"].startswith("vless://")
    assert body["traffic_limit_bytes"] == int(1.0 * 1024 ** 3)
    assert body["subscription_url"].endswith(body["subscription_url"].split("/")[-1])
    # The agent must have been asked to add the user.
    assert any("POST /users" in call[0] for call in mock_node_agent)


def test_config_location_endpoints_only_offer_usable_railway_ws(client, auth_headers, node, inbound, plan, db):
    node.name = "railway-xray"
    node.address = "http://node-agent.railway.internal:8081"
    node.status = NodeStatus.online
    inbound.transport = Transport.ws
    inbound.security = Security.none
    inbound.path = "/vless"
    inbound.public_host = "node-agent-production.up.railway.app"
    inbound.public_port = 443
    inbound.extra = {"railway_ws_tls": True}
    db.commit()

    locations = client.get("/api/v1/services/available-nodes", headers=auth_headers)
    inbounds = client.get("/api/v1/services/available-inbounds", headers=auth_headers)
    assert locations.status_code == 200
    assert len(locations.json()) == 1
    assert inbounds.status_code == 200
    assert len(inbounds.json()) == 1
    assert inbounds.json()[0]["transport"] == "ws"

    inbound.extra = {}
    db.commit()
    assert client.get("/api/v1/services/available-nodes", headers=auth_headers).json() == []


def test_create_service_without_plan_or_inbound_fails(client, auth_headers, normal_user):
    response = client.post(
        "/api/v1/services", headers=auth_headers, json={"user_id": normal_user.id}
    )
    assert response.status_code == 409


def test_quota_enforcement_limits_and_disables(client, auth_headers, normal_user, inbound, plan, db):
    body = _create_service(client, auth_headers, normal_user, inbound, plan)
    service = db.get(Service, body["id"])

    # Simulate the node reporting more traffic than the 1 GiB allowance.
    service.used_up_bytes = int(0.6 * 1024 ** 3)
    service.used_down_bytes = int(0.6 * 1024 ** 3)
    db.commit()

    actions = enforce_quota(db)
    assert any(a["service_id"] == service.id for a in actions)
    db.refresh(service)
    assert service.status == ServiceStatus.limited
    assert not service.is_usable


def test_unlimited_plan_is_never_quota_limited(client, auth_headers, normal_user, inbound, db, plan):
    plan.traffic_gb = 0
    db.commit()
    body = _create_service(client, auth_headers, normal_user, inbound, plan)
    service = db.get(Service, body["id"])
    service.used_up_bytes = 10 * 1024 ** 4
    db.commit()
    assert enforce_quota(db) == []
    db.refresh(service)
    assert service.status == ServiceStatus.active


def test_expiry_moves_service_to_expired(client, auth_headers, normal_user, inbound, plan, db):
    body = _create_service(client, auth_headers, normal_user, inbound, plan)
    service = db.get(Service, body["id"])
    service.expires_at = utcnow() - timedelta(minutes=5)
    db.commit()

    actions = expire_services(db)
    assert any(a["service_id"] == service.id for a in actions)
    db.refresh(service)
    assert service.status == ServiceStatus.expired


def test_renew_extends_expiry_and_reactivates(client, auth_headers, normal_user, inbound, plan, db):
    body = _create_service(client, auth_headers, normal_user, inbound, plan)
    service = db.get(Service, body["id"])
    service.status = ServiceStatus.expired
    service.expires_at = utcnow() - timedelta(days=2)
    db.commit()

    response = client.post(
        f"/api/v1/services/{service.id}/renew",
        headers=auth_headers,
        json={"days": 30, "reset_traffic": True, "add_traffic_gb": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "active"
    assert payload["days_left"] >= 29
    assert payload["used_bytes"] == 0
    assert payload["traffic_limit_bytes"] == int(6.0 * 1024 ** 3)


def test_traffic_delta_accounting(db, normal_user, inbound, plan, node):
    from app.services.provisioning import create_service

    service = create_service(db, user=normal_user, plan=plan, inbound=inbound)

    # First poll: cumulative counters 1 GiB up / 2 GiB down.
    from app.services.provisioning import apply_usage

    apply_usage(db, node, {service.email_tag: {"up": 1024 ** 3, "down": 2 * 1024 ** 3}})
    db.refresh(service)
    assert service.used_up_bytes == 1024 ** 3
    assert service.used_down_bytes == 2 * 1024 ** 3

    # Second poll: only the delta is added, never the absolute value.
    apply_usage(db, node, {service.email_tag: {"up": 1024 ** 3 + 100, "down": 2 * 1024 ** 3 + 200}})
    db.refresh(service)
    assert service.used_up_bytes == 1024 ** 3 + 100
    assert service.used_down_bytes == 2 * 1024 ** 3 + 200


def test_counter_reset_is_handled(db, normal_user, inbound, plan, node):
    from app.services.provisioning import apply_usage, create_service

    service = create_service(db, user=normal_user, plan=plan, inbound=inbound)
    apply_usage(db, node, {service.email_tag: {"up": 5_000_000, "down": 5_000_000}})
    # Node restarted -> counters went backwards; the delta must be the new value.
    apply_usage(db, node, {service.email_tag: {"up": 1_000, "down": 2_000}})
    db.refresh(service)
    assert service.used_up_bytes == 5_000_000 + 1_000
    assert service.used_down_bytes == 5_000_000 + 2_000


def test_bulk_actions(client, auth_headers, normal_user, inbound, plan, db):
    first = _create_service(client, auth_headers, normal_user, inbound, plan)
    second = _create_service(client, auth_headers, normal_user, inbound, plan)
    ids = [first["id"], second["id"]]

    response = client.post(
        "/api/v1/services/bulk", headers=auth_headers, json={"service_ids": ids, "action": "disable"}
    )
    assert response.status_code == 200
    for service_id in ids:
        assert db.get(Service, service_id).status == ServiceStatus.disabled

    response = client.post(
        "/api/v1/services/bulk", headers=auth_headers, json={"service_ids": ids, "action": "enable"}
    )
    assert response.status_code == 200
    for service_id in ids:
        db.refresh(db.get(Service, service_id))
        assert db.get(Service, service_id).status == ServiceStatus.active

    response = client.post(
        "/api/v1/services/bulk", headers=auth_headers, json={"service_ids": ids, "action": "delete"}
    )
    assert response.status_code == 200
    for service_id in ids:
        assert db.get(Service, service_id).status == ServiceStatus.deleted


def test_non_admin_cannot_create_service(client, user_headers, normal_user, inbound, plan):
    response = client.post(
        "/api/v1/services",
        headers=user_headers,
        json={"user_id": normal_user.id, "inbound_id": inbound.id},
    )
    assert response.status_code == 403


def test_user_only_sees_own_services(client, auth_headers, user_headers, normal_user, inbound, plan, db):
    _create_service(client, auth_headers, normal_user, inbound, plan)

    from app.core.security import hash_password
    import uuid as uuid_lib

    other = User(
        uuid=str(uuid_lib.uuid4()),
        username="stranger",
        password_hash=hash_password("stranger123"),
        balance=0,
    )
    db.add(other)
    db.commit()

    listing = client.get("/api/v1/services", headers=user_headers)
    assert listing.status_code == 200
    assert all(item["user_id"] == normal_user.id for item in listing.json()["items"])


def test_node_sync_pushes_inbounds_and_users(client, auth_headers, normal_user, inbound, plan, mock_node_agent):
    _create_service(client, auth_headers, normal_user, inbound, plan)
    response = client.post(f"/api/v1/nodes/{inbound.node_id}/sync", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert any("POST /inbounds/apply" in call[0] for call in mock_node_agent)


def test_inbound_validation_detects_missing_reality_keys(client, auth_headers, db, node):
    from app.db.models import Inbound, Protocol, Security

    inbound = Inbound(
        node_id=node.id, tag="broken", protocol=Protocol.vless, port=8443, security=Security.reality
    )
    db.add(inbound)
    db.commit()

    response = client.get(f"/api/v1/inbounds/{inbound.id}/validate", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert any("Reality" in error for error in body["errors"])


def test_inbound_port_conflict_is_reported(client, auth_headers, inbound, db, node):
    from app.db.models import Inbound, Protocol, Security

    clash = Inbound(node_id=node.id, tag="clash", protocol=Protocol.trojan, port=inbound.port, security=Security.tls)
    db.add(clash)
    db.commit()

    response = client.get(f"/api/v1/inbounds/{clash.id}/validate", headers=auth_headers)
    assert response.status_code == 200
    assert any("already used" in error for error in response.json()["errors"])
