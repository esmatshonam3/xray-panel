"""Xray client link generation, subscription payloads, Clash config and QR codes."""
from __future__ import annotations

import base64
import io
import json
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlencode

from app.db.models import Inbound, Protocol, Security, Service, Transport

# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
_B64_STD = base64.b64encode
_B64_URL = base64.urlsafe_b64encode


def _b64(data: str, urlsafe: bool = False, pad: bool = False) -> str:
    encoder = _B64_URL if urlsafe else _B64_STD
    out = encoder(data.encode("utf-8")).decode("ascii")
    return out if pad else out.rstrip("=")


def _qs(params: dict[str, Any]) -> str:
    """Deterministic query string with empty values dropped."""
    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    if isinstance(clean.get("alpn"), list):
        clean["alpn"] = ",".join(clean["alpn"])
    return urlencode(clean, quote_via=quote, safe="")


def _transport_params(inbound: Inbound) -> dict[str, Any]:
    params: dict[str, Any] = {"type": inbound.transport.value}
    if inbound.transport == Transport.ws:
        params["path"] = inbound.path or "/"
        if inbound.host_header:
            params["host"] = inbound.host_header
    elif inbound.transport == Transport.grpc:
        params["serviceName"] = inbound.service_name or "grpc"
        params["mode"] = "gun"
    elif inbound.transport == Transport.httpupgrade:
        params["path"] = inbound.path or "/"
        if inbound.host_header:
            params["host"] = inbound.host_header
    elif inbound.transport == Transport.xhttp:
        params["path"] = inbound.path or "/"
        params["mode"] = "auto"
    elif inbound.transport == Transport.kcp:
        params["headerType"] = "none"
        params["seed"] = inbound.path or ""
    elif inbound.transport == Transport.quic:
        params["quicSecurity"] = "none"
        params["key"] = inbound.path or ""
    return params


def _security_params(inbound: Inbound) -> dict[str, Any]:
    params: dict[str, Any] = {"security": inbound.security.value}
    if inbound.security == Security.tls:
        params["sni"] = inbound.sni or inbound.display_host
        if inbound.alpn:
            params["alpn"] = list(inbound.alpn)
        params["fp"] = inbound.fingerprint or "chrome"
        if inbound.transport == Transport.tcp and inbound.flow:
            params["flow"] = inbound.flow
    elif inbound.security == Security.reality:
        params["sni"] = inbound.sni or inbound.reality_dest or inbound.display_host
        params["pbk"] = inbound.reality_public_key or ""
        if inbound.reality_short_ids:
            params["sid"] = inbound.reality_short_ids[0]
        params["fp"] = inbound.fingerprint or "chrome"
        params["spx"] = inbound.reality_spider_x or "/"
        params["flow"] = inbound.flow or "xtls-rprx-vision"
    return params


def _remark(inbound: Inbound, service: Service) -> str:
    parts = [inbound.remark or inbound.tag]
    if service.label and service.label != "config":
        parts.append(service.label)
    if inbound.node and inbound.node.region:
        parts.append(inbound.node.region)
    return " | ".join(parts)


# --------------------------------------------------------------------------- #
#  Per-protocol link builders
# --------------------------------------------------------------------------- #
def build_vless_link(inbound: Inbound, service: Service) -> str:
    params = {**_transport_params(inbound), **_security_params(inbound), "encryption": "none"}
    return (
        f"vless://{service.uuid}@{inbound.display_host}:{inbound.display_port}"
        f"?{_qs(params)}#{quote(_remark(inbound, service))}"
    )


def build_vmess_link(inbound: Inbound, service: Service) -> str:
    payload: dict[str, Any] = {
        "v": "2",
        "ps": _remark(inbound, service),
        "add": inbound.display_host,
        "port": str(inbound.display_port),
        "id": service.uuid,
        "aid": "0",
        "scy": "auto",
        "net": inbound.transport.value,
        "type": "none",
        "host": inbound.host_header or "",
        "path": inbound.path or "",
        "tls": "tls" if inbound.security == Security.tls else "",
        "sni": inbound.sni or "",
        "alpn": ",".join(inbound.alpn) if inbound.alpn else "",
        "fp": inbound.fingerprint or "",
    }
    if inbound.transport == Transport.grpc:
        payload["path"] = inbound.service_name or "grpc"
    return f"vmess://{_b64(json.dumps(payload, ensure_ascii=False), pad=True)}"


def build_trojan_link(inbound: Inbound, service: Service) -> str:
    params = {**_transport_params(inbound), **_security_params(inbound)}
    params.pop("flow", None)
    params["security"] = inbound.security.value if inbound.security != Security.none else "tls"
    return (
        f"trojan://{quote(service.uuid, safe='')}@{inbound.display_host}:{inbound.display_port}"
        f"?{_qs(params)}#{quote(_remark(inbound, service))}"
    )


def build_ss_link(inbound: Inbound, service: Service) -> str:
    method = service.ss_method or inbound.ss_method or "chacha20-ietf-poly1305"
    userinfo = _b64(f"{method}:{service.uuid}", urlsafe=True)
    return (
        f"ss://{userinfo}@{inbound.display_host}:{inbound.display_port}"
        f"#{quote(_remark(inbound, service))}"
    )


_BUILDERS = {
    Protocol.vless: build_vless_link,
    Protocol.vmess: build_vmess_link,
    Protocol.trojan: build_trojan_link,
    Protocol.shadowsocks: build_ss_link,
}


def build_link(service: Service) -> str:
    builder = _BUILDERS.get(service.protocol)
    if builder is None:  # pragma: no cover - guarded by enum
        raise ValueError(f"unsupported protocol: {service.protocol}")
    return builder(service.inbound, service)


def build_links(service: Service) -> list[dict[str, str]]:
    """Return every representation of a service for the UI / bot."""
    return [
        {"kind": "link", "label": "VLESS/VMess/Trojan URI", "value": build_link(service)},
        {
            "kind": "subscription",
            "label": "Subscription URL",
            "value": service.subscription_url,
        },
        {"kind": "json", "label": "Share JSON", "value": json.dumps(_share_json(service), ensure_ascii=False, indent=2)},
    ]


def _share_json(service: Service) -> dict[str, Any]:
    inbound = service.inbound
    return {
        "remark": _remark(inbound, service),
        "protocol": service.protocol.value,
        "address": inbound.display_host,
        "port": inbound.display_port,
        "id": service.uuid,
        "transport": inbound.transport.value,
        "security": inbound.security.value,
        "sni": inbound.sni,
        "path": inbound.path,
        "host": inbound.host_header,
        "flow": service.flow,
        "subscription_url": service.subscription_url,
    }


# --------------------------------------------------------------------------- #
#  Subscriptions
# --------------------------------------------------------------------------- #
def build_subscription(services: Iterable[Service], fmt: str = "base64") -> str:
    """fmt: `base64` (v2rayN/Clash.Meta compatible), `plain` or `clash`."""
    usable = [s for s in services if s.protocol in _BUILDERS]
    links = [build_link(s) for s in usable]

    if fmt == "plain":
        return "\n".join(links)
    if fmt == "clash":
        return build_clash_yaml(usable)
    return _b64("\n".join(links))


def build_clash_yaml(services: list[Service]) -> str:
    """Minimal Clash.Meta / mihomo profile."""
    proxies: list[dict[str, Any]] = []
    names: list[str] = []
    for svc in services:
        inbound = svc.inbound
        name = _remark(inbound, svc)
        names.append(name)
        proxy: dict[str, Any] = {
            "name": name,
            "server": inbound.display_host,
            "port": inbound.display_port,
            "type": svc.protocol.value,
            "udp": True,
        }
        if svc.protocol in (Protocol.vless, Protocol.vmess):
            proxy["uuid"] = svc.uuid
            if svc.protocol == Protocol.vmess:
                proxy["alterId"] = 0
                proxy["cipher"] = "auto"
        elif svc.protocol == Protocol.trojan:
            proxy["password"] = svc.uuid
        else:
            proxy["password"] = svc.uuid
            proxy["cipher"] = svc.ss_method or inbound.ss_method or "chacha20-ietf-poly1305"

        if inbound.transport != Transport.tcp:
            proxy["network"] = inbound.transport.value
            opts: dict[str, Any] = {}
            if inbound.transport == Transport.ws:
                opts = {"path": inbound.path or "/", "headers": {"Host": inbound.host_header or inbound.display_host}}
            elif inbound.transport == Transport.grpc:
                opts = {"grpc-service-name": inbound.service_name or "grpc"}
            proxy[inbound.transport.value + "-opts"] = opts

        if inbound.security == Security.tls:
            proxy["tls"] = True
            proxy["servername"] = inbound.sni or inbound.display_host
            if inbound.alpn:
                proxy["alpn"] = list(inbound.alpn)
        elif inbound.security == Security.reality:
            proxy["tls"] = True
            proxy["servername"] = inbound.sni or inbound.reality_dest
            proxy["reality-opts"] = {
                "public-key": inbound.reality_public_key or "",
                "short-id": (inbound.reality_short_ids or [""])[0],
            }
            proxy["client-fingerprint"] = inbound.fingerprint or "chrome"
        if svc.flow:
            proxy["flow"] = svc.flow
        proxies.append(proxy)

    lines: list[str] = [
        "# Generated by Xray Panel - do not edit by hand",
        "mixed-port: 7890",
        "allow-lan: false",
        "mode: rule",
        "log-level: warning",
        "proxies:",
    ]
    for proxy in proxies:
        lines.append("  - " + json.dumps(proxy, ensure_ascii=False))
    lines.append("proxy-groups:")
    lines.append("  - name: PROXY")
    lines.append("    type: select")
    lines.append("    proxies:")
    for name in names or ["DIRECT"]:
        lines.append(f"      - {json.dumps(name, ensure_ascii=False)}")
    lines.append("rules:")
    lines.append("  - MATCH,PROXY")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
#  QR codes
# --------------------------------------------------------------------------- #
def qr_png(data: str, *, box_size: int = 8, border: int = 2) -> bytes:
    import qrcode

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qr_svg(data: str) -> str:
    import qrcode
    import qrcode.image.svg as svg_factory

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(image_factory=svg_factory.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def format_bytes(num: Optional[int]) -> str:
    if num is None:
        return "∞"
    if num == 0:
        return "∞"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(num) < step:
            return f"{num:.2f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= step
    return f"{num:.2f} EB"


def format_gb(num_bytes: Optional[int]) -> str:
    if not num_bytes:
        return "∞"
    return f"{num_bytes / 1024 ** 3:.2f} GB"
