"""DEV-ONLY: populate a running local panel with demo data via the REST API.

Requires the panel (and ideally `tools/dev_stub_agent.py`) to be running.

    python tools/dev_seed_demo.py --base http://127.0.0.1:8000

Creates:
  * 3 configs for the demo user across the seeded inbounds
  * 1 expired config (so the "expired" state is visible)
  * 1 config close to its quota limit
  * 1 pending payment awaiting admin review
It is idempotent-ish: it refuses to run twice if configs already exist.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def call(base: str, method: str, path: str, body: dict | None = None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}/api/v1{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        print(f"  ! {method} {path} -> {exc.code} {detail[:300]}")
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo data into a running panel")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--admin", default="admin")
    parser.add_argument("--admin-password", default="admin12345")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    print(f"→ connecting to {base}")
    status = call(base, "GET", "/status")
    if not status:
        print("✗ panel is not reachable")
        return 1
    print(f"  {status['app']} v{status['version']} · {status['environment']}")

    auth = call(base, "POST", "/auth/login", {"username": args.admin, "password": args.admin_password})
    if not auth:
        print("✗ login failed")
        return 1
    token = auth["access_token"]

    users = call(base, "GET", "/users?q=demo&size=5", token=token)
    customer = next((u for u in users["items"] if u["username"] == "demo"), None)
    if not customer:
        print("✗ demo user not found — run `python -m app.cli init-db --seed --seed-demo` first")
        return 1

    if customer["service_count"]:
        print(f"→ demo user already has {customer['service_count']} config(s); nothing to do")
        return 0

    plans = call(base, "GET", "/plans?size=50", token=token)["items"]
    inbounds = call(base, "GET", "/inbounds", token=token)
    if not plans or not inbounds:
        print("✗ no plans or inbounds available")
        return 1
    print(f"  {len(plans)} plans · {len(inbounds)} inbounds")

    by_code = {p["code"]: p for p in plans}
    by_tag = {i["tag"]: i for i in inbounds}

    wanted = [
        ("vless-reality", "basic", {"label": "Reality — main", "duration_days": 30}),
        ("vmess-ws", "pro", {"label": "VMess — backup", "duration_days": 30}),
        ("trojan-tls", "trial", {"label": "Trojan — trial", "duration_days": 7}),
    ]

    created = []
    for tag, plan_code, extra in wanted:
        inbound = by_tag.get(tag)
        plan = by_code.get(plan_code)
        if not inbound or not plan:
            continue
        payload = {"user_id": customer["id"], "plan_id": plan["id"], "inbound_id": inbound["id"], **extra}
        result = call(base, "POST", "/services", payload, token=token)
        if result:
            created.append(result)
            print(f"  ✓ #{result['id']:<3} {result['label']:<20} {result['protocol']:<12} synced={result['is_synced']}")

    if not created:
        print("✗ no config was created")
        return 1

    # Make one config look expired, and one nearly out of quota.
    first = created[0]
    call(
        base, "PATCH", f"/services/{first['id']}",
        {"expires_at": "2026-01-01T00:00:00+00:00", "status": "expired"}, token=token,
    )
    print(f"  ✓ #{first['id']} marked expired")

    if len(created) > 1:
        second = created[1]
        call(base, "PATCH", f"/services/{second['id']}", {"traffic_gb": 0.05}, token=token)
        print(f"  ✓ #{second['id']} quota tightened to 0.05 GB (for quota tests)")

    # A pending payment so the admin review queue is not empty.
    customer_login = call(base, "POST", "/auth/login", {"username": "demo", "password": "demo12345"})
    if customer_login:
        payment = call(
            base, "POST", "/payments",
            {"plan_id": by_code["basic"]["id"], "method": "manual", "purpose": "purchase"},
            token=customer_login["access_token"],
        )
        if payment:
            print(f"  ✓ pending payment {payment['reference']} ({payment['amount']} {payment['currency']})")

    print("\n✅ demo data ready")
    print(f"   panel  : {base}")
    print(f"   admin  : {args.admin} / {args.admin_password}")
    print("   customer: demo / demo12345")
    return 0


if __name__ == "__main__":
    sys.exit(main())
