"""Wrappers around the `xray api` CLI (HandlerService + StatsService).

The agent deliberately shells out to the official CLI instead of vendoring
protobuf stubs: it removes a whole class of version-skew bugs, and the CLI is
present in every Xray release.

If a CLI signature changes between Xray versions, every call degrades safely:
the caller falls back to a full config regeneration + process restart, which is
always correct (just briefly disruptive).
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Optional

from agent.config import settings

STAT_PATTERN = re.compile(r'name:\s*"([^"]+)"\s*\n?\s*value:\s*(\d+)')


class XrayApiError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 20) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            [settings.xray_bin, "api", *args, f"--server={settings.api_server}"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
#  Users
# --------------------------------------------------------------------------- #
def add_user(inbound_tag: str, protocol: str, user: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """Best-effort hot add. Returns (ok, error)."""
    payload = _user_payload(protocol, user)
    code, out, err = _run(["adu", inbound_tag, json.dumps(payload)])
    if code != 0:
        return False, (err or out).strip()[:400]
    return True, None


def remove_user(inbound_tag: str, email: str) -> tuple[bool, Optional[str]]:
    code, out, err = _run(["rmu", inbound_tag, email])
    if code != 0:
        return False, (err or out).strip()[:400]
    return True, None


def _user_payload(protocol: str, user: dict[str, Any]) -> dict[str, Any]:
    """Shape a panel user descriptor into the protocol specific Xray account."""
    email = user["email"]
    payload: dict[str, Any] = {"email": email, "level": int(user.get("level", 0))}
    if protocol in ("vless", "vmess"):
        account: dict[str, Any] = {"id": user.get("id") or user.get("uuid")}
        if protocol == "vmess":
            account["alterId"] = int(user.get("alter_id", 0))
            account["security"] = user.get("security", "auto")
        elif user.get("flow"):
            account["flow"] = user["flow"]
        payload["account"] = account
    else:  # trojan / shadowsocks
        payload["account"] = {"password": user.get("password") or user.get("id")}
    return payload


# --------------------------------------------------------------------------- #
#  Stats
# --------------------------------------------------------------------------- #
def query_stats(*, pattern: str = "user>>>", reset: bool = False) -> dict[str, dict[str, int]]:
    """Return {email: {"up": bytes, "down": bytes}} from the StatsService."""
    args = ["statsquery", f"--pattern={pattern}"]
    if reset:
        args.append("-reset")
    code, out, err = _run(args, timeout=30)
    if code != 0:
        raise XrayApiError((err or out).strip()[:400] or "statsquery failed")

    result: dict[str, dict[str, int]] = {}
    for name, value in STAT_PATTERN.findall(out):
        # name format: user>>>email>>>traffic>>>uplink|downlink
        parts = name.split(">>>")
        if len(parts) != 4 or parts[0] != "user":
            continue
        email, _, direction = parts[1], parts[2], parts[3]
        bucket = result.setdefault(email, {"up": 0, "down": 0})
        if direction == "uplink":
            bucket["up"] = int(value)
        elif direction == "downlink":
            bucket["down"] = int(value)
    return result


def online_users(pattern: str = "user>>>") -> int:
    """Approximate live user count: entries with non-zero counters."""
    try:
        stats = query_stats(pattern=pattern, reset=False)
    except XrayApiError:
        return 0
    return sum(1 for counters in stats.values() if counters["up"] or counters["down"])


def test_config(path: str) -> tuple[bool, Optional[str]]:
    """`xray run -test -c <path>` — validates before we reload."""
    try:
        proc = subprocess.run(
            [settings.xray_bin, "run", "-test", "-c", path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip()[:800]
    return True, None


def xray_version() -> Optional[str]:
    try:
        proc = subprocess.run(
            [settings.xray_bin, "version"], capture_output=True, text=True, timeout=10, check=False
        )
        first = (proc.stdout or proc.stderr).splitlines()
        return first[0].strip() if first else None
    except (OSError, subprocess.SubprocessError):
        return None


def generate_reality_keys() -> tuple[Optional[str], Optional[str]]:
    """Convenience for bootstrapping Reality inbounds from the agent shell."""
    try:
        proc = subprocess.run(
            [settings.xray_bin, "x25519"], capture_output=True, text=True, timeout=15, check=False
        )
        private = public = None
        for line in proc.stdout.splitlines():
            if "Private" in line or "private" in line:
                private = line.split(":")[-1].strip()
            elif "Public" in line or "public" in line:
                public = line.split(":")[-1].strip()
        return private, public
    except (OSError, subprocess.SubprocessError):
        return None, None
