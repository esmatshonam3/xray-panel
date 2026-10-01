"""Operational CLI:  python -m app.cli <command>

Commands
--------
init-db [--seed]     create tables, optionally seed defaults
seed-demo            create demo plans, a node and sample users
create-admin         create/promote an admin interactively (flags supported)
reset-password       reset a user's password
sync-nodes           push the desired state to every node
check                validate configuration and print a report
"""
from __future__ import annotations

import argparse
import secrets
import sys
import uuid as uuid_lib
from typing import Optional

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.models import (
    Inbound,
    Node,
    Plan,
    Protocol,
    Role,
    Security,
    Transport,
    User,
    UserStatus,
)
from app.db.session import SessionLocal, init_db


def cmd_init_db(args: argparse.Namespace) -> int:
    init_db()
    print("✅ database schema is ready")
    if args.seed:
        with SessionLocal() as db:
            ensure_superadmin(db)
            if args.seed_demo:
                seed_demo(db)
    return 0


def ensure_superadmin(db) -> User:
    user = db.execute(select(User).where(User.username == settings.superadmin_username)).scalar_one_or_none()
    if user:
        if user.role != Role.owner:
            user.role = Role.owner
            db.commit()
            print(f"ℹ️  promoted '{user.username}' to owner")
        return user

    from app.core.security import hash_password

    user = User(
        uuid=str(uuid_lib.uuid4()),
        username=settings.superadmin_username,
        password_hash=hash_password(settings.superadmin_password),
        role=Role.owner,
        status=UserStatus.active,
        telegram_id=settings.superadmin_telegram_id,
        must_change_password=settings.is_production,
        note="bootstrap owner account",
    )
    db.add(user)
    db.commit()
    print(f"✅ owner account created: {user.username}")
    return user


def seed_demo(db) -> None:
    """Idempotent demo dataset so the panel is usable right after deploy."""
    from app.core.security import hash_password

    node = db.execute(select(Node).where(Node.name == "demo-node")).scalar_one_or_none()
    if node is None:
        node = Node(
            name="demo-node",
            address="http://127.0.0.1:8081",
            public_host="node1.example.com",
            region="EU",
            tags=["eu", "default"],
            status=__import__("app.db.models", fromlist=["NodeStatus"]).NodeStatus.unknown,
            max_services=500,
        )
        node.api_token = "demo-node-token-change-me"
        db.add(node)
        db.flush()
        print("✅ demo node created (api token: demo-node-token-change-me)")

        db.add_all(
            [
                Inbound(
                    node_id=node.id,
                    tag="vless-reality",
                    remark="VLESS Reality",
                    protocol=Protocol.vless,
                    port=443,
                    transport=Transport.tcp,
                    security=Security.reality,
                    sni="www.cloudflare.com",
                    flow="xtls-rprx-vision",
                    reality_dest="www.cloudflare.com:443",
                    reality_short_ids=["6ba85179e30d4fc2"],
                    is_default=True,
                    sort_order=100,
                ),
                Inbound(
                    node_id=node.id,
                    tag="vmess-ws",
                    remark="VMess WebSocket",
                    protocol=Protocol.vmess,
                    port=8443,
                    transport=Transport.ws,
                    security=Security.tls,
                    path="/ws",
                    sni="node1.example.com",
                    sort_order=90,
                ),
                Inbound(
                    node_id=node.id,
                    tag="trojan-tls",
                    remark="Trojan TLS",
                    protocol=Protocol.trojan,
                    port=2053,
                    transport=Transport.tcp,
                    security=Security.tls,
                    sni="node1.example.com",
                    sort_order=80,
                ),
            ]
        )

    plans = [
        ("trial", "Trial 7 days", 0.0, 7, 10, ["vless", "vmess"]),
        ("basic", "Basic 30 days", 5.0, 30, 50, ["vless", "vmess", "trojan"]),
        ("pro", "Pro 30 days", 12.0, 30, 150, ["vless", "vmess", "trojan", "shadowsocks"]),
        ("unlimited", "Unlimited 30 days", 25.0, 30, 0, ["vless", "vmess", "trojan"]),
    ]
    for code, name, price, days, gb, protocols in plans:
        if db.execute(select(Plan).where(Plan.code == code)).scalar_one_or_none():
            continue
        db.add(
            Plan(
                code=code,
                name=name,
                price=price,
                duration_days=days,
                traffic_gb=gb,
                allowed_protocols=protocols,
                features=["No logs", "Multi-device", "24/7 support"],
            )
        )

    if not db.execute(select(User).where(User.username == "demo")).scalar_one_or_none():
        db.add(
            User(
                uuid=str(uuid_lib.uuid4()),
                username="demo",
                password_hash=hash_password("demo12345"),
                role=Role.user,
                status=UserStatus.active,
                balance=10.0,
                note="demo customer",
            )
        )
    db.commit()
    print("✅ demo plans + user created (demo / demo12345)")


def cmd_seed_demo(_: argparse.Namespace) -> int:
    with SessionLocal() as db:
        seed_demo(db)
    return 0


def cmd_create_admin(args: argparse.Namespace) -> int:
    from app.core.security import hash_password

    password = args.password or secrets.token_urlsafe(12)
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == args.username)).scalar_one_or_none()
        if user:
            user.role = Role.owner if args.owner else Role.admin
            if args.password:
                user.password_hash = hash_password(password)
            db.commit()
            print(f"✅ '{user.username}' updated to {user.role.value}")
        else:
            user = User(
                uuid=str(uuid_lib.uuid4()),
                username=args.username,
                password_hash=hash_password(password),
                role=Role.owner if args.owner else Role.admin,
                status=UserStatus.active,
                telegram_id=args.telegram_id,
                must_change_password=True,
            )
            db.add(user)
            db.commit()
            print(f"✅ admin '{user.username}' created")
    if not args.password:
        print(f"🔑 generated password: {password}")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    from app.core.security import hash_password

    password = args.password or secrets.token_urlsafe(12)
    with SessionLocal() as db:
        user = db.execute(select(User).where(User.username == args.username)).scalar_one_or_none()
        if user is None:
            print(f"❌ user '{args.username}' not found")
            return 1
        user.password_hash = hash_password(password)
        user.failed_login_count = 0
        user.locked_until = None
        user.must_change_password = True
        db.commit()
    print(f"🔑 new password for {args.username}: {password}")
    return 0


def cmd_sync_nodes(_: argparse.Namespace) -> int:
    from app.services.provisioning import sync_node

    with SessionLocal() as db:
        nodes = list(db.execute(select(Node)).scalars())
        for node in nodes:
            result = sync_node(db, node)
            flag = "✅" if result["ok"] else "❌"
            print(f"{flag} {node.name}: applied={result['applied']} errors={result['errors']}")
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    problems: list[str] = []
    warnings: list[str] = []

    if settings.secret_key.startswith("CHANGE_ME"):
        problems.append("SECRET_KEY is still the default value")
    if settings.is_production and settings.superadmin_password in ("admin", "admin12345", "CHANGE_ME_strong_password"):
        problems.append("SUPERADMIN_PASSWORD is a default value")
    if settings.telegram_enabled and not settings.telegram_bot_token:
        warnings.append("TELEGRAM_ENABLED=true but TELEGRAM_BOT_TOKEN is empty")
    if settings.telegram_enabled and not settings.telegram_webhook_secret:
        warnings.append("TELEGRAM_WEBHOOK_SECRET is empty - the webhook path is guessable")
    if "*" in settings.cors_origin_list and settings.is_production:
        warnings.append("CORS_ORIGINS=* in production")
    if settings.is_sqlite and settings.is_production:
        warnings.append("SQLite in production - use managed Postgres for durability")
    if settings.web_concurrency > 1 and settings.enable_scheduler and settings.is_sqlite:
        warnings.append("Multiple workers with SQLite: only the leader instance will run jobs")

    print(f"app        : {settings.app_name} v{__import__('app').__version__}")
    print(f"environment: {settings.environment}")
    print(f"database   : {settings.sqlalchemy_url.split('://')[0]}")
    print(f"panel url  : {settings.panel_base_url}")
    print(f"scheduler  : {'on' if settings.enable_scheduler else 'off'}")
    print(f"telegram   : {'on' if settings.telegram_bot_token else 'off'}")
    for warning in warnings:
        print(f"⚠️  {warning}")
    for problem in problems:
        print(f"❌ {problem}")
    if not problems and not warnings:
        print("✅ configuration looks good")
    return 1 if problems else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Xray Panel operations CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="create database schema")
    p.add_argument("--seed", action="store_true", help="also create the bootstrap owner")
    p.add_argument("--seed-demo", action="store_true", help="also insert demo plans/node/user")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("seed-demo", help="insert demo data")
    p.set_defaults(func=cmd_seed_demo)

    p = sub.add_parser("create-admin", help="create or promote an admin")
    p.add_argument("username")
    p.add_argument("--password")
    p.add_argument("--telegram-id", type=int)
    p.add_argument("--owner", action="store_true")
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("reset-password", help="reset a user password")
    p.add_argument("username")
    p.add_argument("--password")
    p.set_defaults(func=cmd_reset_password)

    p = sub.add_parser("sync-nodes", help="push desired state to all nodes")
    p.set_defaults(func=cmd_sync_nodes)

    p = sub.add_parser("check", help="validate configuration")
    p.set_defaults(func=cmd_check)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
