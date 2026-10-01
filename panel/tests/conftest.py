"""Pytest fixtures: isolated SQLite database, seeded admin, mocked node agent."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Optional

import pytest

# ---------------------------------------------------------------------------
# Environment must be configured BEFORE importing the application package,
# because `app.core.config.settings` is instantiated at import time.
# ---------------------------------------------------------------------------
_TMP_DIR = Path(tempfile.mkdtemp(prefix="xpanel-tests-"))
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DEBUG": "true",
        "DATABASE_URL": f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}",
        "SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256-signing",
        "SUPERADMIN_USERNAME": "root",
        "SUPERADMIN_PASSWORD": "rootpassword",
        "ENABLE_SCHEDULER": "false",
        "TELEGRAM_ENABLED": "false",
        "TELEGRAM_BOT_TOKEN": "",
        "BACKUP_ENABLED": "false",
        "BACKUP_DIR": str(_TMP_DIR / "backups"),
        "CORS_ORIGINS": "*",
        "QUOTA_ENFORCEMENT_ENABLED": "true",
        "AUTO_DISABLE_ON_EXPIRY": "true",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app.db.models import (  # noqa: E402
    Inbound,
    Node,
    Plan,
    Protocol,
    Role,
    Security,
    Service,
    ServiceStatus,
    Transport,
    User,
    UserStatus,
)
from app.db.base import utcnow  # noqa: E402
from app.db.session import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.node_client import NodeResponse  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    init_db()
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate everything between tests for deterministic assertions."""
    from app.db.base import Base

    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """Limiters are process-global; a throttling test must not leak into others."""
    from app.api.deps import api_limiter
    from app.api.v1.auth import login_limiter
    from app.api.v1.telegram import _webhook_limiter

    for limiter in (api_limiter, login_limiter, _webhook_limiter):
        limiter._hits.clear()
    yield
    for limiter in (api_limiter, login_limiter, _webhook_limiter):
        limiter._hits.clear()


@pytest.fixture(autouse=True)
def mock_node_agent(monkeypatch):
    """Replace all HTTP calls to node agents with deterministic fakes."""
    calls: list[tuple[str, tuple, dict]] = []

    def ok(data=None):
        return NodeResponse(ok=True, status_code=200, data=data or {"ok": True}, latency_ms=3.0)

    def fake_request(self, method, path, payload=None, *, params=None):
        calls.append((f"{method} {path}", (), payload or {}))
        if path == "/health":
            return ok(
                {
                    "ok": True,
                    "xray_running": True,
                    "xray_version": "Xray 1.8.24 (test)",
                    "cpu_percent": 12.5,
                    "memory_percent": 40.0,
                    "disk_percent": 33.0,
                    "uptime_seconds": 86400,
                    "active_users": 3,
                }
            )
        if path == "/stats":
            return ok({"users": {}})
        return ok({"ok": True})

    monkeypatch.setattr("app.services.node_client.NodeClient._request", fake_request)
    return calls


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
#  Data factories
# --------------------------------------------------------------------------- #
@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def admin_user(db) -> User:
    from app.core.security import hash_password

    user = User(
        uuid=str(uuid.uuid4()),
        username="admin",
        password_hash=hash_password("admin"),
        role=Role.owner,
        status=UserStatus.active,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def normal_user(db) -> User:
    from app.core.security import hash_password

    user = User(
        uuid=str(uuid.uuid4()),
        username="customer",
        password_hash=hash_password("customer123"),
        role=Role.user,
        status=UserStatus.active,
        balance=25.0,
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def node(db) -> Node:
    row = Node(
        name="test-node",
        address="http://127.0.0.1:9/agent",
        public_host="node.test",
        region="TEST",
        tags=["test"],
        max_services=0,
    )
    row.api_token = "test-node-token"
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def inbound(db, node) -> Inbound:
    row = Inbound(
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
        reality_short_ids=["abcd1234"],
        is_default=True,
    )
    row.reality_private_key = "PRIVATE_KEY_PLACEHOLDER"
    row.reality_public_key = "PUBLIC_KEY_PLACEHOLDER"
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def plan(db) -> Plan:
    row = Plan(
        code="basic",
        name="Basic",
        price=5.0,
        currency="USD",
        duration_days=30,
        traffic_gb=1.0,  # 1 GiB - small so quota tests are cheap
        max_devices=2,
        allowed_protocols=["vless"],
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def auth_headers(client, admin_user) -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def db_user_factory(db):
    """Create a persisted user with sensible defaults."""

    def factory(
        username: str,
        *,
        role: Role = Role.user,
        status: UserStatus = UserStatus.active,
        balance: float = 0.0,
        telegram_id: Optional[int] = None,
        password: str = "secret12345",
    ) -> User:
        from app.core.security import hash_password

        user = User(
            uuid=str(uuid.uuid4()),
            username=username,
            password_hash=hash_password(password),
            role=role,
            status=status,
            balance=balance,
            telegram_id=telegram_id,
        )
        db.add(user)
        db.commit()
        return user

    return factory


@pytest.fixture
def make_service(db, node, inbound):
    """Provision a service directly, bypassing the HTTP layer."""

    def factory(user: User, *, plan: Optional[Plan] = None, inbound_override=None, **overrides) -> Service:
        from app.core.security import generate_sub_token, generate_uuid

        target_inbound = inbound_override or inbound
        service = Service(
            user_id=user.id,
            plan_id=plan.id if plan else None,
            node_id=target_inbound.node_id,
            inbound_id=target_inbound.id,
            label=overrides.pop("label", "test-config"),
            protocol=target_inbound.protocol,
            uuid=generate_uuid(),
            email_tag=f"{user.username}.{uuid.uuid4().hex[:8]}",
            sub_token=generate_sub_token(),
            flow=target_inbound.flow,
            status=ServiceStatus.active,
            started_at=utcnow(),
            expires_at=utcnow() + timedelta(days=30),
            traffic_limit_bytes=int((plan.traffic_gb if plan else 10) * 1024 ** 3),
            **overrides,
        )
        db.add(service)
        db.commit()
        return service

    return factory


@pytest.fixture
def user_headers(client, normal_user) -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"username": "customer", "password": "customer123"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
