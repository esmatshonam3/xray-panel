"""Client link, subscription and QR generation."""
from __future__ import annotations

import base64
import json

import pytest

from app.db.models import Protocol, Security, Service, ServiceStatus, Transport
from app.services.xray_links import (
    build_clash_yaml,
    build_link,
    build_subscription,
    format_bytes,
    qr_png,
)


@pytest.fixture
def make_service(db, normal_user, node):
    from app.core.security import generate_sub_token, generate_uuid

    def factory(
        protocol: Protocol = Protocol.vless,
        transport: Transport = Transport.tcp,
        security: Security = Security.reality,
        **kwargs,
    ) -> Service:
        from app.db.models import Inbound

        inbound = Inbound(
            node_id=node.id,
            tag=kwargs.pop("tag", f"{protocol.value}-{transport.value}"),
            protocol=protocol,
            port=kwargs.pop("port", 443),
            transport=transport,
            security=security,
            sni=kwargs.pop("sni", "www.cloudflare.com"),
            path=kwargs.pop("path", "/ws"),
            host_header=kwargs.pop("host_header", "cdn.example.com"),
            service_name=kwargs.pop("service_name", "grpcsvc"),
            flow=kwargs.pop("flow", "xtls-rprx-vision"),
            ss_method=kwargs.pop("ss_method", "chacha20-ietf-poly1305"),
        )
        if security == Security.reality:
            inbound.reality_private_key = "PRIV"
            inbound.reality_public_key = "PUBKEY123"
            inbound.reality_short_ids = ["abcd1234"]
            inbound.reality_dest = "www.cloudflare.com:443"
        db.add(inbound)
        db.flush()

        service = Service(
            user_id=normal_user.id,
            node_id=node.id,
            inbound_id=inbound.id,
            label="test-config",
            protocol=protocol,
            uuid=generate_uuid() if protocol in (Protocol.vless, Protocol.vmess) else generate_sub_token(),
            email_tag=f"customer.{protocol.value}.test",
            sub_token=generate_sub_token(),
            flow=inbound.flow,
            ss_method=inbound.ss_method,
            status=ServiceStatus.active,
        )
        db.add(service)
        db.commit()
        return service

    return factory


def test_vless_reality_link(make_service):
    service = make_service(Protocol.vless, Transport.tcp, Security.reality)
    link = build_link(service)
    assert link.startswith("vless://")
    assert service.uuid in link
    assert "security=reality" in link
    assert "pbk=PUBKEY123" in link
    assert "flow=xtls-rprx-vision" in link
    assert "type=tcp" in link


def test_vless_ws_tls_link(make_service):
    service = make_service(Protocol.vless, Transport.ws, Security.tls)
    link = build_link(service)
    assert "type=ws" in link
    assert "security=tls" in link
    assert "path=%2Fws%2Fws" in link
    assert "host=cdn.example.com" in link


def test_railway_public_domain_ws_uses_edge_tls(make_service, db, node):
    service = make_service(Protocol.vless, Transport.ws, Security.none, tag="railway-ws")
    service.inbound.node.name = "railway-xray"
    service.inbound.public_host = "node-agent-production.up.railway.app"
    service.inbound.public_port = 443
    service.inbound.extra = {"railway_ws_tls": True}

    link = build_link(service)
    assert "@node-agent-production.up.railway.app:443" in link
    assert "type=ws" in link
    assert "security=tls" in link
    assert "sni=node-agent-production.up.railway.app" in link
    assert "alpn=http%2F1.1" in link
    assert "path=%2Fws" in link


def test_vmess_link_is_base64_json(make_service):
    service = make_service(Protocol.vmess, Transport.ws, Security.tls)
    link = build_link(service)
    assert link.startswith("vmess://")
    payload = json.loads(base64.b64decode(link[len("vmess://"):]).decode())
    assert payload["id"] == service.uuid
    assert payload["add"] == "node.test"
    assert payload["port"] == "443"
    assert payload["net"] == "ws"


def test_trojan_link(make_service):
    service = make_service(Protocol.trojan, Transport.tcp, Security.tls)
    link = build_link(service)
    assert link.startswith("trojan://")
    assert service.uuid in link
    assert "security=tls" in link


def test_shadowsocks_link(make_service):
    service = make_service(Protocol.shadowsocks, Transport.tcp, Security.none)
    link = build_link(service)
    assert link.startswith("ss://")
    userinfo = link[len("ss://"):].split("@")[0]
    decoded = base64.urlsafe_b64decode(userinfo + "=" * (-len(userinfo) % 4)).decode()
    assert decoded.startswith("chacha20-ietf-poly1305:")


def test_subscription_base64_roundtrip(make_service):
    services = [
        make_service(Protocol.vless, Transport.tcp, Security.reality, tag="a"),
        make_service(Protocol.trojan, Transport.tcp, Security.tls, tag="b"),
    ]
    body = build_subscription(services, fmt="base64")
    decoded = base64.b64decode(body + "=" * (-len(body) % 4)).decode()
    assert "vless://" in decoded
    assert "trojan://" in decoded
    assert len(decoded.splitlines()) == 2


def test_subscription_plain_and_clash(make_service):
    services = [make_service(Protocol.vless, Transport.ws, Security.tls, tag="c")]
    plain = build_subscription(services, fmt="plain")
    assert plain.startswith("vless://")

    clash = build_clash_yaml(services)
    assert "proxies:" in clash
    assert "proxy-groups:" in clash
    assert "MATCH,PROXY" in clash
    assert '"network": "ws"' in clash
    assert '"type": "vless"' in clash


def test_qr_png_is_valid_image(make_service):
    service = make_service(Protocol.vless, Transport.tcp, Security.reality)
    png = qr_png(build_link(service))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png) > 200


def test_format_bytes():
    assert format_bytes(0) == "∞"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1024 ** 3) == "1.00 GB"
    assert format_bytes(None) == "∞"


def test_builtin_websocket_vless_link_uses_panel_domain(make_service, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "live_proxy_enabled", True)
    monkeypatch.setattr(settings, "panel_base_url", "https://panel.example.com")
    service = make_service(Protocol.vless, Transport.ws, Security.none)
    link = build_link(service)
    assert f"@panel.example.com:443" in link
    assert f"path=%2Fws%2F{service.uuid}" in link
    assert "security=tls" in link
    assert f"host=panel.example.com" in link
