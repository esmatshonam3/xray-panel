"""Node agent configuration (environment driven)."""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    node_name: str = "node-1"
    node_token: str = "CHANGE_ME_node_token"
    public_host: str = ""
    railway_tcp_proxy_domain: str = ""
    railway_tcp_proxy_port: int = 0
    log_level: str = "INFO"

    xray_bin: str = "/usr/local/bin/xray"
    xray_config_path: str = "/etc/xray/config.json"
    xray_state_path: str = "/etc/xray/state.json"
    xray_api_port: int = 10085
    xray_api_host: str = "127.0.0.1"
    xray_log_path: str = "/etc/xray/access.log"
    xray_log_level: str = "warning"

    agent_port: int = 8081
    # Reject requests whose timestamp drifts more than this (replay protection).
    signature_ttl_seconds: int = 300
    allow_insecure_signature: bool = False

    # Applied when the panel has not pushed any inbound yet.
    bootstrap_inbound_port: int = 443

    @property
    def api_server(self) -> str:
        return f"{self.xray_api_host}:{self.xray_api_port}"

    @property
    def token_list(self) -> List[str]:
        return [t.strip() for t in self.node_token.split(",") if t.strip()]


@lru_cache(maxsize=1)
def get_settings() -> AgentSettings:
    return AgentSettings()


settings = get_settings()
