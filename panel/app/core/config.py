"""Application settings loaded from environment variables (12-factor)."""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Complex values are kept as strings so that
    plain comma separated env vars work on Render without JSON quoting."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- general
    app_name: str = "Xray Panel"
    environment: str = "production"
    debug: bool = False
    log_level: str = "INFO"
    panel_base_url: str = "http://localhost:8000"

    # --------------------------------------------------------------- security
    secret_key: str = "CHANGE_ME_please_generate_a_long_random_string_64_chars"
    encryption_key: Optional[str] = None
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 720
    refresh_token_ttl_days: int = 30
    cors_origins: str = "*"
    trusted_hosts: str = "*"
    session_cookie_name: str = "xpanel_session"
    session_cookie_secure: bool = True

    # login throttling
    login_max_attempts: int = 8
    login_window_seconds: int = 900
    api_rate_limit_per_minute: int = 240

    # ------------------------------------------------------- bootstrap account
    superadmin_username: str = "admin"
    # Convenient local bootstrap. Production must set a unique password.
    superadmin_password: str = "admin"
    superadmin_telegram_id: Optional[int] = None

    # --------------------------------------------------------------- database
    database_url: str = "sqlite:///./data/panel.db"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ------------------------------------------------------------- node agent
    node_request_timeout: float = 15.0
    node_verify_tls: bool = True
    node_heartbeat_stale_seconds: int = 180

    # --------------------------------------------------------------- telegram
    telegram_enabled: bool = True
    telegram_bot_token: Optional[str] = None
    telegram_bot_username: Optional[str] = None
    telegram_webhook_secret: Optional[str] = None
    telegram_admin_ids: str = ""
    telegram_auto_set_webhook: bool = True
    telegram_payment_instructions: str = (
        "Send the exact amount to the card below, then upload the receipt here."
    )

    # ---------------------------------------------------------- business rules
    default_currency: str = "USD"
    traffic_sync_interval_seconds: int = 120
    quota_enforcement_enabled: bool = True
    grace_period_hours: int = 24
    auto_disable_on_expiry: bool = True
    subscription_path_prefix: str = "sub"
    referral_bonus_percent: int = 10

    # ---------------------------------------------------------------- backups
    backup_enabled: bool = True
    backup_dir: str = "./data/backups"
    backup_interval_hours: int = 24
    backup_retention_days: int = 14

    # ----------------------------------------------------------------- alerts
    alerts_enabled: bool = True
    alert_webhook_url: Optional[str] = None
    alert_cpu_threshold: float = 90.0
    alert_memory_threshold: float = 90.0
    alert_disk_threshold: float = 90.0

    # ------------------------------------------------------------- web server
    port: int = 8000
    web_concurrency: int = 1
    enable_scheduler: bool = True

    # ------------------------------------------------------------ normalisers
    @property
    def sqlalchemy_url(self) -> str:
        """Render hands out `postgres://` URLs; SQLAlchemy 2 needs a driver."""
        url = (self.database_url or "").strip()
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_url.startswith("sqlite")

    @staticmethod
    def _split(raw: str) -> List[str]:
        raw = (raw or "").strip()
        if not raw or raw == "*":
            return ["*"] if raw == "*" else []
        return [part.strip() for part in raw.split(",") if part.strip()]

    @property
    def cors_origin_list(self) -> List[str]:
        return self._split(self.cors_origins) or ["*"]

    @property
    def trusted_host_list(self) -> List[str]:
        return self._split(self.trusted_hosts) or ["*"]

    @property
    def telegram_admin_id_list(self) -> List[int]:
        out: List[int] = []
        for part in self._split(self.telegram_admin_ids):
            try:
                out.append(int(part))
            except ValueError:
                continue
        if self.superadmin_telegram_id:
            out.append(int(self.superadmin_telegram_id))
        return sorted(set(out))

    @property
    def webhook_url(self) -> str:
        base = self.panel_base_url.rstrip("/")
        secret = self.telegram_webhook_secret or "webhook"
        return f"{base}/api/v1/telegram/webhook/{secret}"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def public_dict(self) -> dict:
        """Safe subset that may be exposed to the UI / logs."""
        return {
            "app_name": self.app_name,
            "environment": self.environment,
            "version": __import__("app").__version__,
            "panel_base_url": self.panel_base_url,
            "telegram_enabled": bool(self.telegram_enabled and self.telegram_bot_token),
            "telegram_bot_username": self.telegram_bot_username,
            "scheduler_enabled": self.enable_scheduler,
            "default_currency": self.default_currency,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
