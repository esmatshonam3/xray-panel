"""Pydantic v2 request/response schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.db.models import (
    AlertLevel,
    NodeStatus,
    PaymentMethod,
    PaymentStatus,
    Protocol,
    Role,
    Security,
    ServiceStatus,
    Transport,
    UserStatus,
)

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20
    pages: int = 1

    @classmethod
    def build(cls, items: list[T], total: int, page: int, size: int) -> "Page[T]":
        pages = max((total + size - 1) // size, 1)
        return cls(items=items, total=total, page=page, size=size, pages=pages)


class Message(BaseModel):
    detail: str
    ok: bool = True


# --------------------------------------------------------------------------- #
#  Auth
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    totp_code: Optional[str] = Field(default=None, max_length=8)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if v.isdigit() or v.isalpha():
            raise ValueError("password must mix letters and digits")
        return v


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    email: Optional[EmailStr] = None
    referral_code: Optional[str] = None


# --------------------------------------------------------------------------- #
#  Users
# --------------------------------------------------------------------------- #
class UserBase(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: Optional[EmailStr] = None
    role: Role = Role.user
    status: UserStatus = UserStatus.active
    note: Optional[str] = None


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)
    balance: float = 0.0


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    role: Optional[Role] = None
    status: Optional[UserStatus] = None
    note: Optional[str] = None
    balance: Optional[float] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class UserOut(ORMModel):
    id: int
    uuid: str
    username: str
    email: Optional[str]
    role: Role
    status: UserStatus
    balance: float
    telegram_id: Optional[int]
    telegram_username: Optional[str]
    referral_code: Optional[str]
    last_login_at: Optional[datetime]
    created_at: datetime
    note: Optional[str] = None
    service_count: int = 0
    total_used_bytes: int = 0


class UserMe(ORMModel):
    id: int
    uuid: str
    username: str
    email: Optional[str]
    role: Role
    status: UserStatus
    balance: float
    telegram_id: Optional[int]
    referral_code: Optional[str]
    totp_enabled: bool = False
    created_at: datetime


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str


# --------------------------------------------------------------------------- #
#  Plans
# --------------------------------------------------------------------------- #
class PlanBase(BaseModel):
    code: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=2, max_length=128)
    description: Optional[str] = None
    price: float = Field(ge=0)
    currency: str = "USD"
    duration_days: int = Field(gt=0, le=3650)
    traffic_gb: float = Field(ge=0)
    max_devices: int = Field(default=2, ge=1, le=100)
    allowed_protocols: list[Protocol] = []
    node_group: Optional[str] = None
    inbound_ids: list[int] = []
    configs_included: int = Field(default=1, ge=1, le=50)
    is_active: bool = True
    is_public: bool = True
    sort_order: int = 0
    features: list[str] = []


class PlanCreate(PlanBase):
    pass


class PlanUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = None
    duration_days: Optional[int] = Field(default=None, gt=0)
    traffic_gb: Optional[float] = Field(default=None, ge=0)
    max_devices: Optional[int] = Field(default=None, ge=1)
    allowed_protocols: Optional[list[Protocol]] = None
    node_group: Optional[str] = None
    inbound_ids: Optional[list[int]] = None
    is_active: Optional[bool] = None
    is_public: Optional[bool] = None
    sort_order: Optional[int] = None
    features: Optional[list[str]] = None


class PlanOut(ORMModel):
    id: int
    code: str
    name: str
    description: Optional[str]
    price: float
    currency: str
    duration_days: int
    traffic_gb: float
    max_devices: int
    allowed_protocols: list[Any] = []
    node_group: Optional[str]
    configs_included: int
    is_active: bool
    is_public: bool
    sort_order: int
    features: list[Any] = []


# --------------------------------------------------------------------------- #
#  Nodes & inbounds
# --------------------------------------------------------------------------- #
class NodeBase(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    address: str = Field(min_length=4, max_length=255)
    public_host: str = Field(min_length=1, max_length=255)
    region: Optional[str] = None
    tags: list[str] = []
    is_active: bool = True
    weight: int = Field(default=100, ge=1, le=1000)
    max_services: int = Field(default=0, ge=0)


class NodeCreate(NodeBase):
    api_token: str = Field(min_length=8, max_length=255)


class NodeUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    public_host: Optional[str] = None
    region: Optional[str] = None
    tags: Optional[list[str]] = None
    is_active: Optional[bool] = None
    weight: Optional[int] = Field(default=None, ge=1)
    max_services: Optional[int] = Field(default=None, ge=0)
    api_token: Optional[str] = None
    status: Optional[NodeStatus] = None


class NodeOut(ORMModel):
    id: int
    name: str
    address: str
    public_host: str
    region: Optional[str]
    tags: list[Any] = []
    status: NodeStatus
    is_active: bool
    weight: int
    max_services: int
    xray_version: Optional[str]
    last_heartbeat_at: Optional[datetime]
    cpu_percent: Optional[float]
    memory_percent: Optional[float]
    disk_percent: Optional[float]
    uptime_seconds: Optional[int]
    online_users: Optional[int]
    last_error: Optional[str]
    service_count: int = 0


class NodeHealth(BaseModel):
    node_id: int
    name: str
    status: NodeStatus
    reachable: bool
    latency_ms: Optional[float] = None
    xray_running: Optional[bool] = None
    xray_version: Optional[str] = None
    cpu_percent: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    uptime_seconds: Optional[int] = None
    active_users: Optional[int] = None
    error: Optional[str] = None
    checked_at: datetime


class InboundBase(BaseModel):
    node_id: int
    tag: str = Field(min_length=1, max_length=64)
    remark: Optional[str] = None
    protocol: Protocol
    port: int = Field(ge=1, le=65535)
    listen: str = "0.0.0.0"
    transport: Transport = Transport.tcp
    security: Security = Security.none
    sni: Optional[str] = None
    alpn: list[str] = []
    fingerprint: str = "chrome"
    reality_public_key: Optional[str] = None
    reality_short_ids: list[str] = []
    reality_dest: Optional[str] = None
    path: Optional[str] = None
    host_header: Optional[str] = None
    service_name: Optional[str] = None
    flow: Optional[str] = None
    ss_method: Optional[str] = None
    public_host: Optional[str] = None
    public_port: Optional[int] = None
    is_active: bool = True
    is_default: bool = False
    sort_order: int = 0


class InboundCreate(InboundBase):
    reality_private_key: Optional[str] = None
    ss_password: Optional[str] = None


class InboundUpdate(BaseModel):
    remark: Optional[str] = None
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    transport: Optional[Transport] = None
    security: Optional[Security] = None
    sni: Optional[str] = None
    alpn: Optional[list[str]] = None
    reality_public_key: Optional[str] = None
    reality_private_key: Optional[str] = None
    reality_short_ids: Optional[list[str]] = None
    reality_dest: Optional[str] = None
    path: Optional[str] = None
    host_header: Optional[str] = None
    service_name: Optional[str] = None
    flow: Optional[str] = None
    public_host: Optional[str] = None
    public_port: Optional[int] = None
    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    sort_order: Optional[int] = None


class InboundOut(ORMModel):
    id: int
    node_id: int
    tag: str
    remark: Optional[str]
    protocol: Protocol
    port: int
    transport: Transport
    security: Security
    sni: Optional[str]
    path: Optional[str]
    host_header: Optional[str]
    service_name: Optional[str]
    flow: Optional[str]
    public_host: Optional[str]
    public_port: Optional[int]
    is_active: bool
    is_default: bool
    sort_order: int
    service_count: int = 0


class InboundCheck(BaseModel):
    ok: bool
    errors: list[str] = []
    warnings: list[str] = []


# --------------------------------------------------------------------------- #
#  Services (configs)
# --------------------------------------------------------------------------- #
class ServiceCreate(BaseModel):
    user_id: int
    plan_id: Optional[int] = None
    inbound_id: Optional[int] = None
    node_id: Optional[int] = None
    label: Optional[str] = None
    duration_days: Optional[int] = Field(default=None, gt=0)
    traffic_gb: Optional[float] = Field(default=None, ge=0)
    connection_limit: int = Field(default=0, ge=0, le=100000)
    ip_limit: int = Field(default=0, ge=0, le=100000)
    speed_limit_mbps: float = Field(default=0, ge=0, le=100000)
    expires_at: Optional[datetime] = None
    note: Optional[str] = None
    auto_renew: bool = False


class ServiceUpdate(BaseModel):
    label: Optional[str] = None
    status: Optional[ServiceStatus] = None
    traffic_gb: Optional[float] = Field(default=None, ge=0)
    expires_at: Optional[datetime] = None
    auto_renew: Optional[bool] = None
    note: Optional[str] = None
    inbound_id: Optional[int] = None
    flow: Optional[str] = None


class ServiceRenew(BaseModel):
    days: int = Field(gt=0, le=3650)
    reset_traffic: bool = False
    add_traffic_gb: float = Field(default=0, ge=0)


class ServiceOut(ORMModel):
    id: int
    user_id: int
    username: Optional[str] = None
    plan_id: Optional[int]
    plan_name: Optional[str] = None
    node_id: int
    node_name: Optional[str] = None
    inbound_id: int
    inbound_tag: Optional[str] = None
    label: str
    protocol: Protocol
    uuid: str
    email_tag: str
    status: ServiceStatus
    started_at: datetime
    expires_at: Optional[datetime]
    days_left: Optional[int] = None
    traffic_limit_bytes: int
    connection_limit: int = 0
    ip_limit: int = 0
    speed_limit_mbps: float = 0
    used_up_bytes: int
    used_down_bytes: int
    used_bytes: int = 0
    usage_percent: float = 0.0
    last_synced_at: Optional[datetime]
    last_connected_at: Optional[datetime]
    is_synced: bool
    auto_renew: bool
    note: Optional[str]
    subscription_url: Optional[str] = None
    created_at: datetime


class ServiceDetail(ServiceOut):
    links: list[dict[str, str]] = []
    raw_link: Optional[str] = None
    subscription_url: Optional[str] = None
    qr_png_url: Optional[str] = None


class ServiceBulkAction(BaseModel):
    service_ids: list[int]
    action: Literal["enable", "disable", "delete", "reset_traffic", "sync"]


# --------------------------------------------------------------------------- #
#  Payments
# --------------------------------------------------------------------------- #
class PaymentCreate(BaseModel):
    plan_id: int
    node_id: Optional[int] = None
    method: PaymentMethod = PaymentMethod.manual
    purpose: Literal["purchase", "renewal"] = "purchase"
    service_id: Optional[int] = None
    receipt_url: Optional[str] = None


class PaymentReview(BaseModel):
    approve: bool
    reason: Optional[str] = None


class PaymentOut(ORMModel):
    id: int
    reference: str
    user_id: int
    username: Optional[str] = None
    plan_id: Optional[int]
    plan_name: Optional[str] = None
    service_id: Optional[int]
    purpose: str
    amount: float
    currency: str
    method: PaymentMethod
    status: PaymentStatus
    provider_ref: Optional[str]
    receipt_url: Optional[str]
    reject_reason: Optional[str]
    paid_at: Optional[datetime]
    created_at: datetime


# --------------------------------------------------------------------------- #
#  Reports / monitoring
# --------------------------------------------------------------------------- #
class TrafficPoint(BaseModel):
    day: str
    up_bytes: int
    down_bytes: int
    total_bytes: int


class DashboardStats(BaseModel):
    users_total: int
    users_active: int
    users_new_today: int
    services_total: int
    services_active: int
    services_expiring_7d: int
    nodes_total: int
    nodes_online: int
    traffic_today_bytes: int
    traffic_month_bytes: int
    revenue_month: float
    currency: str
    alerts_active: int
    pending_payments: int


class TopConsumer(BaseModel):
    service_id: int
    username: str
    label: str
    used_bytes: int
    limit_bytes: int
    usage_percent: float


class NodeMetricPoint(BaseModel):
    recorded_at: datetime
    cpu_percent: Optional[float]
    memory_percent: Optional[float]
    disk_percent: Optional[float]
    online_users: Optional[int]


class AlertOut(ORMModel):
    id: int
    fingerprint: str
    level: AlertLevel
    source: str
    code: str
    title: str
    message: str
    entity_type: Optional[str]
    entity_id: Optional[int]
    is_active: bool
    occurrences: int
    notified: bool
    created_at: datetime
    resolved_at: Optional[datetime]


class AlertCreate(BaseModel):
    code: str
    title: str
    message: str = ""
    level: AlertLevel = AlertLevel.warning
    source: str = "system"
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    meta: dict[str, Any] = {}


class AuditLogOut(ORMModel):
    id: int
    created_at: datetime
    actor_id: Optional[int]
    actor_type: str
    actor_label: Optional[str]
    action: str
    entity_type: Optional[str]
    entity_id: Optional[int]
    status: str
    ip_address: Optional[str]
    meta: dict[str, Any] = {}


class HealthComponent(BaseModel):
    name: str
    status: Literal["ok", "degraded", "down"]
    detail: Optional[str] = None
    latency_ms: Optional[float] = None


class HealthReport(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    environment: str
    uptime_seconds: int
    checked_at: datetime
    components: list[HealthComponent]
    nodes_online: int = 0
    nodes_total: int = 0


class SettingOut(ORMModel):
    key: str
    value: Any
    description: Optional[str]
    is_secret: bool
    updated_at: datetime


class SettingUpdate(BaseModel):
    value: Any
    description: Optional[str] = None


# --------------------------------------------------------------------------- #
#  Telegram bot configuration (editable from the panel)
# --------------------------------------------------------------------------- #
class TelegramConfigIn(BaseModel):
    """Every field is optional: only what you send gets changed."""

    bot_token: Optional[str] = Field(default=None, max_length=128)
    bot_username: Optional[str] = Field(default=None, max_length=64)
    webhook_secret: Optional[str] = Field(default=None, max_length=128)
    admin_ids: Optional[list[int]] = None
    enabled: Optional[bool] = None
    auto_set_webhook: Optional[bool] = None
    # Explicitly wipe the stored token (and fall back to the environment).
    clear_token: bool = False

    @field_validator("bot_token")
    @classmethod
    def _token_shape(cls, v: Optional[str]) -> Optional[str]:
        # An empty value means "leave the stored token alone".
        if v in (None, ""):
            return v
        v = v.strip()
        bot_id, _, secret = v.partition(":")
        if not bot_id.isdigit() or len(secret) < 10:
            raise ValueError("a bot token looks like 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw")
        return v

    @field_validator("bot_username")
    @classmethod
    def _username_shape(cls, v: Optional[str]) -> Optional[str]:
        if v in (None, ""):
            return v
        v = v.strip().lstrip("@")
        if not v.replace("_", "").isalnum():
            raise ValueError("username may only contain letters, digits and underscores")
        return v

    @field_validator("admin_ids")
    @classmethod
    def _ids(cls, v: Optional[list[int]]) -> Optional[list[int]]:
        if v is None:
            return v
        out: list[int] = []
        for raw in v:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"'{raw}' is not a numeric Telegram ID")
            if value <= 0:
                raise ValueError("Telegram IDs are positive numbers")
            out.append(value)
        return sorted(set(out))


class TelegramConfigOut(BaseModel):
    enabled: bool
    has_token: bool
    token_hint: Optional[str] = None
    bot_username: Optional[str] = None
    webhook_secret_set: bool = False
    webhook_secret_hint: Optional[str] = None
    admin_ids: list[int] = []
    auto_set_webhook: bool = False
    webhook_url: str
    # Where the effective values come from: "database" or "environment"
    token_source: str = "environment"
    admin_ids_source: str = "environment"


class TelegramValidation(BaseModel):
    ok: bool
    bot: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class TelegramTestResult(BaseModel):
    ok: bool
    delivered: int = 0
    recipients: list[int] = []
    error: Optional[str] = None


class NodeSyncResult(BaseModel):
    node_id: int
    node_name: str
    ok: bool
    applied: int = 0
    removed: int = 0
    error: Optional[str] = None
