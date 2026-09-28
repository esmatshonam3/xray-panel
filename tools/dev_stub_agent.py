"""DEV-ONLY stub of the Xray node agent.

Purpose: let you exercise the whole panel on a laptop where you cannot run
`xray-core` (Windows, macOS, or a machine without a public IP). It speaks
exactly the same wire protocol as `node-agent/agent/main.py`, but keeps all
state in memory and never touches a real Xray process.

    python tools/dev_stub_agent.py            # listens on 127.0.0.1:8081

Then register a node in the panel with:
    address     = http://127.0.0.1:8081
    api_token   = anything (auth is not enforced by the stub)

DO NOT deploy this. It accepts every request without verification.
"""
from __future__ import annotations

import argparse
import random
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Xray Node Agent (dev stub)", docs_url="/docs")

STATE: dict[str, Any] = {
    "started_at": time.time(),
    "inbounds": {},           # tag -> spec
    "users": {},              # tag -> {email: descriptor}
    "counters": {},           # tag -> {email: {"up": int, "down": int}}
}


def _snapshot() -> dict[str, Any]:
    return {
        "cpu_percent": round(random.uniform(4, 38), 2),
        "memory_percent": round(random.uniform(22, 61), 2),
        "disk_percent": round(random.uniform(12, 44), 2),
        "uptime_seconds": int(time.time() - STATE["started_at"]),
        "net_in_bytes": random.randint(10 ** 6, 10 ** 8),
        "net_out_bytes": random.randint(10 ** 6, 10 ** 8),
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "node": "dev-stub",
        "xray_running": True,
        "xray_version": "Xray 1.8.24 (dev stub)",
        "pid": 12345,
        "inbounds": len(STATE["inbounds"]),
        "users": sum(len(v) for v in STATE["users"].values()),
        "active_users": sum(len(v) for v in STATE["users"].values()),
        **_snapshot(),
    }


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"pong": "dev-stub"}


@app.get("/health/deep")
def deep() -> dict[str, Any]:
    return {**health(), "ok": True, "error": None}


@app.get("/system")
def system() -> dict[str, Any]:
    return {"ok": True, "node": "dev-stub", "public_host": "127.0.0.1", **_snapshot()}


@app.get("/stats")
def stats(reset: bool = False) -> dict[str, Any]:
    """Simulate cumulative counters that grow a little on every poll."""
    users: dict[str, dict[str, int]] = {}
    for tag, members in STATE["users"].items():
        for email in members:
            bucket = STATE["counters"].setdefault(tag, {}).setdefault(email, {"up": 0, "down": 0})
            bucket["up"] += random.randint(2 * 10 ** 5, 3 * 10 ** 6)
            bucket["down"] += random.randint(10 ** 6, 2 * 10 ** 7)
            users[email] = dict(bucket)
    if reset:
        for tag in STATE["counters"]:
            STATE["counters"][tag] = {}
    return {"ok": True, "users": users, "count": len(users)}


@app.post("/users/apply")
async def apply_users(request: Request) -> dict[str, Any]:
    body = await request.json()
    tag = body.get("inbound_tag")
    users = body.get("users", [])
    STATE["users"][tag] = {u["email"]: u for u in users}
    STATE["counters"].setdefault(tag, {})
    return {"ok": True, "added": len(users), "removed": 0, "updated": 0, "hot_applied": True, "reloaded": False}


@app.post("/users")
async def add_user(request: Request) -> dict[str, Any]:
    body = await request.json()
    tag = body["inbound_tag"]
    user = body["user"]
    STATE["users"].setdefault(tag, {})[user["email"]] = user
    return {"ok": True, "added": 1, "removed": 0, "updated": 0, "hot_applied": True, "reloaded": False}


@app.delete("/users/{inbound_tag}/{email}")
def remove_user(inbound_tag: str, email: str) -> dict[str, Any]:
    STATE["users"].get(inbound_tag, {}).pop(email, None)
    STATE["counters"].get(inbound_tag, {}).pop(email, None)
    return {"ok": True, "added": 0, "removed": 1, "updated": 0, "hot_applied": True, "reloaded": False}


@app.post("/inbounds/apply")
async def apply_inbounds(request: Request) -> dict[str, Any]:
    body = await request.json()
    inbounds = body.get("inbounds", [])
    previous = set(STATE["inbounds"])
    STATE["inbounds"] = {spec["tag"]: spec for spec in inbounds}
    for tag in STATE["inbounds"]:
        STATE["users"].setdefault(tag, {})
    return {
        "ok": True,
        "error": None,
        "inbounds": len(inbounds),
        "added": sorted(set(STATE["inbounds"]) - previous),
        "removed": sorted(previous - set(STATE["inbounds"])),
    }


@app.get("/config/{tag}")
def inbound_config(tag: str) -> JSONResponse:
    if tag not in STATE["inbounds"]:
        return JSONResponse(status_code=404, content={"ok": False, "error": f"unknown inbound '{tag}'"})
    return JSONResponse(
        {
            "ok": True,
            "inbound": STATE["inbounds"][tag],
            "clients": list(STATE["users"].get(tag, {}).values()),
        }
    )


@app.post("/restart")
def restart() -> dict[str, Any]:
    STATE["started_at"] = time.time()
    return {"ok": True, "xray_running": True, "restarted": True}


@app.post("/reality-keys")
def reality_keys() -> dict[str, Any]:
    import base64
    import os

    return {
        "ok": True,
        "private_key": base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("="),
        "public_key": base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("="),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Dev stub for the Xray node agent")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    import uvicorn

    print(f"[dev-stub] node agent listening on http://{args.host}:{args.port}")
    print("[dev-stub] register this node in the panel with address = http://127.0.0.1:8081")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
