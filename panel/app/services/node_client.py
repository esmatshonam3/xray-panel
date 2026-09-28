"""HTTP client for node agents (the Xray data plane).

The agent exposes a tiny authenticated REST surface:

    GET    /health                  -> process + system snapshot
    POST   /users/apply             -> full desired-state apply for one inbound
    POST   /users                   -> hot-add a single user
    DELETE /users/{tag}/{email}     -> hot-remove a single user
    GET    /stats                   -> per-user traffic counters
    POST   /restart                 -> restart the Xray process
    GET    /config/{tag}            -> effective inbound config (debug)

Auth: `Authorization: Bearer <node-token>` plus an HMAC signature over the
body when `sign=true`, which protects against token replay on plain HTTP.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import Node

log = get_logger(__name__)


@dataclass
class NodeResponse:
    ok: bool
    status_code: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    latency_ms: Optional[float] = None


class NodeClient:
    def __init__(self, node: Node, *, timeout: Optional[float] = None) -> None:
        self.node = node
        self.token = node.api_token
        self.base_url = node.address.rstrip("/")
        self.timeout = timeout or settings.node_request_timeout

    # ------------------------------------------------------------------ utils
    def _sign(self, body: bytes) -> dict[str, str]:
        ts = str(int(time.time()))
        mac = hmac.new(self.token.encode("utf-8"), ts.encode("ascii") + body, hashlib.sha256)
        return {
            "Authorization": f"Bearer {self.token}",
            "X-Node-Timestamp": ts,
            "X-Node-Signature": mac.hexdigest(),
            "Content-Type": "application/json",
            "User-Agent": f"xpanel/{settings.app_name}",
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> NodeResponse:
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        headers = self._sign(body)
        url = f"{self.base_url}{path}"
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout, verify=settings.node_verify_tls) as client:
                resp = client.request(method, url, content=body or None, headers=headers, params=params)
            latency = round((time.perf_counter() - started) * 1000, 2)
            if resp.status_code >= 400:
                return NodeResponse(
                    ok=False,
                    status_code=resp.status_code,
                    error=f"HTTP {resp.status_code}: {resp.text[:300]}",
                    latency_ms=latency,
                )
            try:
                data = resp.json()
            except ValueError:
                data = {"raw": resp.text[:500]}
            return NodeResponse(ok=bool(data.get("ok", True)), status_code=resp.status_code, data=data, latency_ms=latency)
        except httpx.HTTPError as exc:
            latency = round((time.perf_counter() - started) * 1000, 2)
            log.warning("node request failed", extra={"node": self.node.name, "path": path, "error": str(exc)})
            return NodeResponse(ok=False, error=f"{type(exc).__name__}: {exc}", latency_ms=latency)

    # ---------------------------------------------------------------- surface
    def health(self) -> NodeResponse:
        return self._request("GET", "/health")

    def system(self) -> NodeResponse:
        return self._request("GET", "/system")

    def apply_users(self, inbound_tag: str, users: list[dict[str, Any]], *, protocol: str) -> NodeResponse:
        return self._request(
            "POST",
            "/users/apply",
            {"inbound_tag": inbound_tag, "protocol": protocol, "users": users},
        )

    def apply_inbounds(self, inbounds: list[dict[str, Any]], *, drop_orphan_users: bool = True) -> NodeResponse:
        """Push the whole inbound definition set; the agent renders config.json."""
        return self._request(
            "POST",
            "/inbounds/apply",
            {"inbounds": inbounds, "drop_orphan_users": drop_orphan_users},
        )

    def reality_keys(self) -> NodeResponse:
        return self._request("POST", "/reality-keys", {})

    def add_user(self, inbound_tag: str, user: dict[str, Any], *, protocol: str) -> NodeResponse:
        return self._request(
            "POST", "/users", {"inbound_tag": inbound_tag, "protocol": protocol, "user": user}
        )

    def remove_user(self, inbound_tag: str, email: str) -> NodeResponse:
        return self._request("DELETE", f"/users/{inbound_tag}/{email}")

    def stats(self, *, reset: bool = False) -> NodeResponse:
        return self._request("GET", "/stats", params={"reset": str(reset).lower()})

    def restart(self) -> NodeResponse:
        return self._request("POST", "/restart", {})

    def inbound_config(self, tag: str) -> NodeResponse:
        return self._request("GET", f"/config/{tag}")


def build_user_payload(service) -> dict[str, Any]:
    """Translate a Service row into the node agent's user descriptor."""
    payload: dict[str, Any] = {
        "email": service.email_tag,
        "level": 0,
        "enable": service.is_usable,
    }
    if service.protocol.value in ("vless", "vmess"):
        payload["id"] = service.uuid
        if service.flow:
            payload["flow"] = service.flow
    else:  # trojan / shadowsocks share the password field
        payload["password"] = service.uuid
    return payload
