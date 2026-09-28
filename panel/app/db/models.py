"""Domain model.

Entity map
----------
User            end customer or staff account (role drives RBAC)
Plan            sellable product: duration + traffic quota + allowed protocols
Node            a VPS running the node-agent + Xray core
Inbound         a listening Xray inbound (protocol/transport/security) on a node
Service         a provisioned Xray config = user x inbound, with quota + expiry
TrafficDaily    per-service per-day usage, powers the consumption reports
Payment         purchase / renewal order (manual, crypto, stars, gateway, balance)
Alert           system + service alerting with acknowledgement workflow
AuditLog        immutable trail of every privileged action
Setting         runtime-editable non-secret configuration
BotUser         Telegram <-> panel identity mapping + conversational state
BackupRecord    produced database backups
"""
from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, as_utc, utcnow


# --------------------------------------------------------------------------- #
#  Enumerations
# --------------------------------------------------------------------------- #
class Role(str, enum.Enum):
    user = "user"
    support = "support"
    admin = "admin"
    owner = "owner"


class UserStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    limited = "limited"
    pending = "pending"


class ServiceStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    expired = "expired"
    limited = "limited"      # quota exhausted
    disabled = "disabled"    # manually suspended
    deleted = "deleted"


class Protocol(str, enum.Enum):
    vless = "vless"
    vmess = "vmess"
    trojan = "trojan"
    shadowsocks = "shadowsocks"


class Transport(str, enum.Enum):
    tcp = "tcp"
    ws = "ws"
    grpc = "grpc"
    httpupgrade = "httpupgrade"
    xhttp = "xhttp"
    kcp = "kcp"
    quic = "quic"


class Security(str, enum.Enum):
    none = "none"
    tls = "tls"
    reality = "reality"


class NodeStatus(str, enum.Enum):
    online = "online"
    offline = "offline"
    degraded = "degraded"
    unknown = "unknown"
    maintenance = "maintenance"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    awaiting_review = "awaiting_review"
    paid = "paid"
    failed = "failed"
    canceled = "canceled"
    refunded = "refunded"


class PaymentMethod(str, enum.Enum):
    manual = "manual"            # card-to-card / receipt upload
    crypto = "crypto"
    telegram_stars = "telegram_stars"
    gateway = "gateway"          # external PSP callback
    balance = "balance"          # pay from wallet


class AlertLevel(str, enum.Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class AlertSource(str, enum.Enum):
    system = "system"
    node = "node"
    service = "service"
    payment = "payment"
    security = "security"


# --------------------------------------------------------------------------- #
#  Identity
# --------------------------------------------------------------------------- #
class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[Role] = mapped_column(SAEnum(Role, native_enum=False, length=16), default=Role.user, nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, native_enum=False, length=16), default=UserStatus.active, nullable=False, index=True
    )

    # Telegram identity (unique per bot)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(64))
    telegram_language: Mapped[str] = mapped_column(String(8), default="fa")

    # Wallet + referral
    balance: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    referral_code: Mapped[Optional[str]] = mapped_column(String(16), unique=True, index=True)
    referred_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    referral_earnings: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Security
    api_key_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    totp_secret: Mapped[Optional[str]] = mapped_column(String(64))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(64))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Preferences / metadata
    note: Mapped[Optional[str]] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    services: Mapped[list["Service"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", foreign_keys="Payment.user_id"
    )

    @property
    def is_staff(self) -> bool:
        return self.role in (Role.support, Role.admin, Role.owner)

    @property
    def is_admin(self) -> bool:
        return self.role in (Role.admin, Role.owner)


class BotUser(Base, TimestampMixin):
    """Conversational state + identity link for the Telegram bot."""

    __tablename__ = "bot_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    language: Mapped[str] = mapped_column(String(8), default="fa")
    state: Mapped[Optional[str]] = mapped_column(String(64))
    state_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    user: Mapped[Optional["User"]] = relationship(lazy="joined")


# --------------------------------------------------------------------------- #
#  Infrastructure
# --------------------------------------------------------------------------- #
class Node(Base, TimestampMixin):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)          # api base url
    public_host: Mapped[str] = mapped_column(String(255), nullable=False)      # host used inside configs
    region: Mapped[Optional[str]] = mapped_column(String(64))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    # Encrypted at rest with Fernet.
    api_token_enc: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[NodeStatus] = mapped_column(
        SAEnum(NodeStatus, native_enum=False, length=16), default=NodeStatus.unknown, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    max_services: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0 = unlimited

    # Health snapshot
    xray_version: Mapped[Optional[str]] = mapped_column(String(32))
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    cpu_percent: Mapped[Optional[float]] = mapped_column(Float)
    memory_percent: Mapped[Optional[float]] = mapped_column(Float)
    disk_percent: Mapped[Optional[float]] = mapped_column(Float)
    uptime_seconds: Mapped[Optional[int]] = mapped_column(BigInteger)
    online_users: Mapped[Optional[int]] = mapped_column(Integer)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    inbounds: Mapped[list["Inbound"]] = relationship(
        back_populates="node", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def api_token(self) -> str:
        from app.core.security import decrypt

        return decrypt(self.api_token_enc)

    @api_token.setter
    def api_token(self, value: str) -> None:
        from app.core.security import encrypt

        self.api_token_enc = encrypt(value)


class Inbound(Base, TimestampMixin):
    """A listening Xray inbound. One inbound can host many Services."""

    __tablename__ = "inbounds"
    __table_args__ = (UniqueConstraint("node_id", "tag", name="uq_inbound_node_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    tag: Mapped[str] = mapped_column(String(64), nullable=False)
    remark: Mapped[Optional[str]] = mapped_column(String(128))
    protocol: Mapped[Protocol] = mapped_column(SAEnum(Protocol, native_enum=False, length=16), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    listen: Mapped[str] = mapped_column(String(64), default="0.0.0.0")

    transport: Mapped[Transport] = mapped_column(
        SAEnum(Transport, native_enum=False, length=16), default=Transport.tcp, nullable=False
    )
    security: Mapped[Security] = mapped_column(
        SAEnum(Security, native_enum=False, length=16), default=Security.none, nullable=False
    )

    # TLS / Reality
    sni: Mapped[Optional[str]] = mapped_column(String(255))
    alpn: Mapped[list[str]] = mapped_column(JSON, default=list)
    fingerprint: Mapped[str] = mapped_column(String(32), default="chrome")
    reality_public_key: Mapped[Optional[str]] = mapped_column(String(128))
    reality_private_key_enc: Mapped[Optional[str]] = mapped_column(Text)
    reality_short_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    reality_dest: Mapped[Optional[str]] = mapped_column(String(255))
    reality_spider_x: Mapped[Optional[str]] = mapped_column(String(255))

    # Transport tuning
    path: Mapped[Optional[str]] = mapped_column(String(255))
    host_header: Mapped[Optional[str]] = mapped_column(String(255))
    service_name: Mapped[Optional[str]] = mapped_column(String(128))   # gRPC
    flow: Mapped[Optional[str]] = mapped_column(String(64))            # e.g. xtls-rprx-vision

    # Shadowsocks
    ss_method: Mapped[Optional[str]] = mapped_column(String(48))

    # Public endpoint advertised in client configs (may differ from bind port,
    # e.g. behind a load balancer / CDN).
    public_host: Mapped[Optional[str]] = mapped_column(String(255))
    public_port: Mapped[Optional[int]] = mapped_column(Integer)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    node: Mapped["Node"] = relationship(back_populates="inbounds", lazy="joined")
    services: Mapped[list["Service"]] = relationship(back_populates="inbound")

    @property
    def reality_private_key(self) -> str:
        from app.core.security import decrypt

        return decrypt(self.reality_private_key_enc or "")

    @reality_private_key.setter
    def reality_private_key(self, value: str) -> None:
        from app.core.security import encrypt

        self.reality_private_key_enc = encrypt(value) if value else None

    @property
    def display_host(self) -> str:
        return self.public_host or self.node.public_host

    @property
    def display_port(self) -> int:
        return self.public_port or self.port


# --------------------------------------------------------------------------- #
#  Commerce
# --------------------------------------------------------------------------- #
class Plan(Base, TimestampMixin):
    __tablename__ = "plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    price: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    duration_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    traffic_gb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0 = unlimited

    max_devices: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    allowed_protocols: Mapped[list[str]] = mapped_column(JSON, default=list)
    node_group: Mapped[Optional[str]] = mapped_column(String(64))   # matches Node.tags
    inbound_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    configs_included: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    features: Mapped[list[str]] = mapped_column(JSON, default=list)

    services: Mapped[list["Service"]] = relationship(back_populates="plan")

    @property
    def traffic_bytes(self) -> int:
        return int(self.traffic_gb * 1024 ** 3)


class Service(Base, TimestampMixin):
    """A provisioned Xray configuration belonging to a user."""

    __tablename__ = "services"
    __table_args__ = (
        UniqueConstraint("inbound_id", "email_tag", name="uq_service_inbound_email"),
        Index("ix_service_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"), index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    inbound_id: Mapped[int] = mapped_column(ForeignKey("inbounds.id", ondelete="CASCADE"), nullable=False, index=True)

    label: Mapped[str] = mapped_column(String(128), default="config")
    protocol: Mapped[Protocol] = mapped_column(SAEnum(Protocol, native_enum=False, length=16), nullable=False)
    uuid: Mapped[str] = mapped_column(String(64), nullable=False)          # vless/vmess id or trojan/ss password
    email_tag: Mapped[str] = mapped_column(String(128), nullable=False)    # Xray stats identity
    sub_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    flow: Mapped[Optional[str]] = mapped_column(String(64))
    ss_method: Mapped[Optional[str]] = mapped_column(String(48))

    status: Mapped[ServiceStatus] = mapped_column(
        SAEnum(ServiceStatus, native_enum=False, length=16),
        default=ServiceStatus.active,
        nullable=False,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    traffic_limit_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)  # 0 = unlimited
    used_up_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    used_down_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Snapshot of the previous counter reading from the node (delta accounting)
    last_raw_up: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_raw_down: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    is_synced: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_error: Mapped[Optional[str]] = mapped_column(Text)
    note: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="services", lazy="joined")
    plan: Mapped[Optional["Plan"]] = relationship(back_populates="services", lazy="joined")
    node: Mapped["Node"] = relationship(lazy="joined")
    inbound: Mapped["Inbound"] = relationship(back_populates="services", lazy="joined")

    # ---------------------------------------------------------------- helpers
    @property
    def used_bytes(self) -> int:
        return self.used_up_bytes + self.used_down_bytes

    @property
    def remaining_bytes(self) -> Optional[int]:
        if not self.traffic_limit_bytes:
            return None
        return max(self.traffic_limit_bytes - self.used_bytes, 0)

    @property
    def usage_percent(self) -> float:
        if not self.traffic_limit_bytes:
            return 0.0
        return round(self.used_bytes / self.traffic_limit_bytes * 100, 2)

    @property
    def days_left(self) -> Optional[int]:
        expiry = as_utc(self.expires_at)
        if expiry is None:
            return None
        delta = expiry - utcnow()
        return max(delta.days, 0)

    @property
    def is_expired(self) -> bool:
        expiry = as_utc(self.expires_at)
        return bool(expiry and expiry <= utcnow())

    @property
    def is_quota_exhausted(self) -> bool:
        return bool(self.traffic_limit_bytes and self.used_bytes >= self.traffic_limit_bytes)

    @property
    def is_usable(self) -> bool:
        return self.status == ServiceStatus.active and not self.is_expired and not self.is_quota_exhausted

    @property
    def subscription_url(self) -> str:
        from app.core.config import settings

        return f"{settings.panel_base_url.rstrip('/')}/sub/{self.sub_token}"


class Payment(Base, TimestampMixin):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id: Mapped[Optional[int]] = mapped_column(ForeignKey("plans.id", ondelete="SET NULL"))
    service_id: Mapped[Optional[int]] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"))
    purpose: Mapped[str] = mapped_column(String(16), default="purchase")  # purchase | renewal | topup

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    method: Mapped[PaymentMethod] = mapped_column(
        SAEnum(PaymentMethod, native_enum=False, length=24), default=PaymentMethod.manual, nullable=False
    )
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, native_enum=False, length=24),
        default=PaymentStatus.pending,
        nullable=False,
        index=True,
    )

    provider_ref: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    receipt_file_id: Mapped[Optional[str]] = mapped_column(String(255))   # Telegram file_id
    receipt_url: Mapped[Optional[str]] = mapped_column(String(512))
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    user: Mapped["User"] = relationship(back_populates="payments", foreign_keys=[user_id], lazy="joined")
    plan: Mapped[Optional["Plan"]] = relationship(lazy="joined")


class TrafficDaily(Base):
    __tablename__ = "traffic_daily"
    __table_args__ = (UniqueConstraint("service_id", "day", name="uq_traffic_service_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    up_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    down_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    @property
    def total_bytes(self) -> int:
        return self.up_bytes + self.down_bytes


# --------------------------------------------------------------------------- #
#  Observability
# --------------------------------------------------------------------------- #
class NodeMetric(Base):
    __tablename__ = "node_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    cpu_percent: Mapped[Optional[float]] = mapped_column(Float)
    memory_percent: Mapped[Optional[float]] = mapped_column(Float)
    disk_percent: Mapped[Optional[float]] = mapped_column(Float)
    online_users: Mapped[Optional[int]] = mapped_column(Integer)
    net_in_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    net_out_bytes: Mapped[Optional[int]] = mapped_column(BigInteger)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float)


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    level: Mapped[AlertLevel] = mapped_column(
        SAEnum(AlertLevel, native_enum=False, length=16), default=AlertLevel.warning, nullable=False, index=True
    )
    source: Mapped[AlertSource] = mapped_column(
        SAEnum(AlertSource, native_enum=False, length=16), default=AlertSource.system, nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    entity_type: Mapped[Optional[str]] = mapped_column(String(32))
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    acknowledged_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_type: Mapped[str] = mapped_column(String(16), default="user")   # user | bot | system | node
    actor_label: Mapped[Optional[str]] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(16), default="success")
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Setting(Base, TimestampMixin):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(32), default="local")   # local | s3 | external
    status: Mapped[str] = mapped_column(String(16), default="ok")
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
