"""Desired-state store, config rendering and the supervised Xray process.

State file layout (`XRAY_STATE_PATH`)
-------------------------------------
{
  "inbounds": [ {tag, protocol, port, transport, security, ...}, ... ],
  "users": { "<inbound_tag>": [ {email, id|password, flow, level, enable}, ... ] }
}

The panel is the single source of truth: it pushes the whole inbound list and
the user list of an inbound. The agent renders `config.json`, validates it with
`xray run -test`, then either hot-applies user changes through the API or
restarts the process when the inbound set changed.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from agent.config import settings
from agent import xray_api


class XrayManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._process: Optional[subprocess.Popen] = None
        self._state: dict[str, Any] = {"inbounds": [], "users": {}}
        self._started_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._config_hash: Optional[str] = None
        self._load_state()

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def _load_state(self) -> None:
        path = Path(settings.xray_state_path)
        if path.exists():
            try:
                self._state = json.loads(path.read_text("utf-8"))
            except (OSError, ValueError):
                self._state = {"inbounds": [], "users": {}}
        self._state.setdefault("inbounds", [])
        self._state.setdefault("users", {})

    def _save_state(self) -> None:
        path = Path(settings.xray_state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=2), "utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        running = self._process is not None and self._process.poll() is None
        return {
            "xray_running": running,
            "xray_version": xray_api.xray_version(),
            "pid": self._process.pid if running and self._process else None,
            "uptime_seconds": int(time.time() - self._started_at) if running and self._started_at else 0,
            "inbounds": len(self._state.get("inbounds", [])),
            "users": sum(len(v) for v in self._state.get("users", {}).values()),
            "last_error": self._last_error,
        }

    # --------------------------------------------------------------- rendering
    def render_config(self) -> dict[str, Any]:
        inbounds: list[dict[str, Any]] = []
        for spec in self._state.get("inbounds", []):
            inbound = self._render_inbound(spec, self._state.get("users", {}).get(spec["tag"], []))
            if inbound:
                inbounds.append(inbound)

        return {
            "log": {"loglevel": settings.xray_log_level, "access": settings.xray_log_path, "error": ""},
            "api": {"tag": "api", "services": ["HandlerService", "LoggerService", "StatsService"]},
            "stats": {},
            "policy": {
                "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}},
                "system": {"statsInboundUplink": True, "statsInboundDownlink": True},
            },
            "routing": {
                "domainStrategy": "AsIs",
                "rules": [
                    {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                    {"type": "field", "protocol": ["bittorrent"], "outboundTag": "block"},
                ],
            },
            "inbounds": inbounds,
            "outbounds": [
                {"protocol": "freedom", "tag": "direct"},
                {"protocol": "blackhole", "tag": "block"},
            ],
        }

    def _render_inbound(self, spec: dict[str, Any], users: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        protocol = (spec.get("protocol") or "vless").lower()
        clients = [self._render_client(protocol, u) for u in users if u.get("enable", True)]

        stream: dict[str, Any] = {"network": spec.get("transport", "tcp"), "security": spec.get("security", "none")}
        transport = spec.get("transport", "tcp")
        if transport == "ws":
            stream["wsSettings"] = {"path": spec.get("path") or "/"}
            if spec.get("host_header"):
                stream["wsSettings"]["headers"] = {"Host": spec["host_header"]}
        elif transport == "grpc":
            stream["grpcSettings"] = {"serviceName": spec.get("service_name") or "grpc"}
        elif transport == "httpupgrade":
            stream["httpupgradeSettings"] = {"path": spec.get("path") or "/"}
            if spec.get("host_header"):
                stream["httpupgradeSettings"]["host"] = spec["host_header"]
        elif transport == "xhttp":
            stream["xhttpSettings"] = {"path": spec.get("path") or "/", "mode": "auto"}
        elif transport == "tcp":
            stream["tcpSettings"] = {"header": {"type": "none"}}

        if spec.get("security") == "tls":
            stream["tlsSettings"] = {
                "serverName": spec.get("sni") or settings.public_host,
                "alpn": spec.get("alpn") or ["h2", "http/1.1"],
                "certificates": [
                    {
                        "certificateFile": spec.get("cert_file", "/etc/xray/cert.pem"),
                        "keyFile": spec.get("key_file", "/etc/xray/key.pem"),
                    }
                ],
            }
        elif spec.get("security") == "reality":
            stream["realitySettings"] = {
                "show": False,
                "dest": spec.get("reality_dest") or f"{spec.get('sni', 'www.cloudflare.com')}:443",
                "xver": 0,
                "serverNames": [spec.get("sni") or "www.cloudflare.com"],
                "privateKey": spec.get("reality_private_key", ""),
                "shortIds": spec.get("reality_short_ids") or [""],
            }
            if spec.get("reality_spider_x"):
                stream["realitySettings"]["spiderX"] = spec["reality_spider_x"]

        inbound: dict[str, Any] = {
            "tag": spec["tag"],
            "listen": spec.get("listen", "0.0.0.0"),
            "port": int(spec["port"]),
            "protocol": protocol,
            "settings": {},
            "streamSettings": stream,
            "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": False},
        }

        if protocol == "vless":
            inbound["settings"] = {"clients": clients, "decryption": "none", "fallbacks": []}
        elif protocol == "vmess":
            inbound["settings"] = {"clients": clients}
        elif protocol == "trojan":
            inbound["settings"] = {"clients": clients, "fallbacks": []}
        elif protocol == "shadowsocks":
            method = spec.get("ss_method") or "chacha20-ietf-poly1305"
            inbound["settings"] = {"method": method, "password": spec.get("ss_password") or "", "network": "tcp,udp"}
            if clients:
                # Multi-user shadowsocks (2022) uses the clients array.
                inbound["settings"] = {"method": method, "clients": clients, "network": "tcp,udp"}
        else:
            return None
        return inbound

    @staticmethod
    def _render_client(protocol: str, user: dict[str, Any]) -> dict[str, Any]:
        client: dict[str, Any] = {"email": user["email"], "level": int(user.get("level", 0))}
        if protocol in ("vless", "vmess"):
            client["id"] = user.get("id") or user.get("uuid")
            if protocol == "vmess":
                client["alterId"] = int(user.get("alter_id", 0))
                client["security"] = user.get("security", "auto")
            elif user.get("flow"):
                client["flow"] = user["flow"]
        else:
            client["password"] = user.get("password") or user.get("id")
        return client

    # ------------------------------------------------------------- apply users
    def apply_users(self, inbound_tag: str, protocol: str, users: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace the user set of one inbound."""
        with self._lock:
            spec = self._find_inbound(inbound_tag)
            if spec is None:
                return {"ok": False, "error": f"unknown inbound '{inbound_tag}'"}

            current = {u["email"]: u for u in self._state["users"].get(inbound_tag, [])}
            desired = {u["email"]: u for u in users}

            added = [e for e in desired if e not in current]
            removed = [e for e in current if e not in desired]
            updated = [
                e for e in desired
                if e in current and json.dumps(current[e], sort_keys=True) != json.dumps(desired[e], sort_keys=True)
            ]

            self._state["users"][inbound_tag] = list(desired.values())
            self._save_state()

            hot_ok = True
            errors: list[str] = []
            for email in removed:
                ok, err = xray_api.remove_user(inbound_tag, email)
                hot_ok = hot_ok and ok
                if err:
                    errors.append(f"rmu {email}: {err}")
            for email in added + updated:
                ok, err = xray_api.add_user(inbound_tag, protocol, desired[email])
                hot_ok = hot_ok and ok
                if err:
                    errors.append(f"adu {email}: {err}")

            reloaded = False
            if not hot_ok:
                # Any hot failure => deterministic full reload.
                ok, err = self.reload()
                reloaded = ok
                if not ok:
                    return {
                        "ok": False,
                        "error": f"hot apply failed and reload failed: {err}",
                        "hot_errors": errors,
                    }

            return {
                "ok": True,
                "added": len(added),
                "removed": len(removed),
                "updated": len(updated),
                "hot_applied": hot_ok,
                "reloaded": reloaded,
                "warnings": errors,
            }

    # --------------------------------------------------------- apply inbounds
    def apply_inbounds(self, inbounds: list[dict[str, Any]], *, drop_orphan_users: bool = True) -> dict[str, Any]:
        with self._lock:
            previous_tags = {spec["tag"] for spec in self._state.get("inbounds", [])}
            new_tags = {spec["tag"] for spec in inbounds}
            self._state["inbounds"] = inbounds

            users = self._state.get("users", {})
            if drop_orphan_users:
                for tag in list(users):
                    if tag not in new_tags:
                        users.pop(tag, None)
            for tag in new_tags:
                users.setdefault(tag, [])

            self._save_state()
            ok, error = self.reload()
            return {
                "ok": ok,
                "error": error,
                "inbounds": len(inbounds),
                "added": sorted(new_tags - previous_tags),
                "removed": sorted(previous_tags - new_tags),
            }

    # ------------------------------------------------------------ process mgmt
    def write_config(self) -> tuple[bool, Optional[str]]:
        config = self.render_config()
        path = Path(settings.xray_config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, indent=2), "utf-8")
        ok, error = xray_api.test_config(str(tmp))
        if not ok:
            tmp.unlink(missing_ok=True)
            self._last_error = f"config validation failed: {error}"
            return False, self._last_error
        tmp.replace(path)
        self._config_hash = str(hash(json.dumps(config, sort_keys=True)))
        return True, None

    def start(self) -> tuple[bool, Optional[str]]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return True, None
            ok, error = self.write_config()
            if not ok:
                return False, error
            try:
                log_path = Path(settings.xray_log_path)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                self._process = subprocess.Popen(
                    [settings.xray_bin, "run", "-c", settings.xray_config_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self._started_at = time.time()
                self._last_error = None
            except OSError as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                return False, self._last_error

        time.sleep(1.0)
        if self._process and self._process.poll() is not None:
            self._last_error = f"xray exited immediately with code {self._process.returncode}"
            return False, self._last_error
        return True, None

    def stop(self) -> None:
        with self._lock:
            if self._process is None:
                return
            if self._process.poll() is None:
                try:
                    os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    self._process.terminate()
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
            self._process = None
            self._started_at = None

    def reload(self) -> tuple[bool, Optional[str]]:
        """Graceful restart: stop, validate new config, start."""
        self.stop()
        time.sleep(0.4)
        return self.start()

    def ensure_running(self) -> tuple[bool, Optional[str]]:
        """Called by the health endpoint; restarts a crashed core."""
        if self._process is not None and self._process.poll() is None:
            return True, None
        return self.start()

    # --------------------------------------------------------------- accessors
    def _find_inbound(self, tag: str) -> Optional[dict[str, Any]]:
        for spec in self._state.get("inbounds", []):
            if spec.get("tag") == tag:
                return spec
        return None

    def inbound_clients(self, tag: str) -> list[dict[str, Any]]:
        return list(self._state.get("users", {}).get(tag, []))

    def stats(self, *, reset: bool = False) -> dict[str, dict[str, int]]:
        return xray_api.query_stats(reset=reset)


manager = XrayManager()
