"""Configuration management with environment variable support."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (walk up from this file)
_root = Path(__file__).resolve().parent.parent
for _candidate in [_root / ".env", Path.cwd() / ".env"]:
    if _candidate.exists():
        load_dotenv(_candidate)
        break


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int = 0) -> int:
    v = os.getenv(key)
    return int(v) if v else default


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key, "").lower()
    return v in ("1", "true", "yes") if v else default


def _env_list(key: str, default: str = "") -> list[str]:
    v = os.getenv(key, default)
    return [s.strip() for s in v.split(",") if s.strip()] if v else []


@dataclass
class Settings:
    """All configuration, populated from environment variables."""

    # SSH
    ssh_host: str = field(default_factory=lambda: _env("NEXUS_SSH_HOST", "127.0.0.1"))
    ssh_port: int = field(default_factory=lambda: _env_int("NEXUS_SSH_PORT", 22))
    ssh_user: str = field(default_factory=lambda: _env("NEXUS_SSH_USER", "root"))
    ssh_password: str = field(default_factory=lambda: _env("NEXUS_SSH_PASSWORD", ""))
    ssh_key_path: str = field(default_factory=lambda: _env("NEXUS_SSH_KEY_PATH", ""))
    ssh_pool_size: int = field(default_factory=lambda: _env_int("NEXUS_SSH_POOL_SIZE", 4))

    # OAuth2
    oauth_client_id: str = field(default_factory=lambda: _env("NEXUS_OAUTH_CLIENT_ID", "nexus-default"))
    oauth_client_secret: str = field(default_factory=lambda: _env("NEXUS_OAUTH_CLIENT_SECRET", ""))
    oauth_issuer: str = field(default_factory=lambda: _env("NEXUS_OAUTH_ISSUER", ""))

    # Server
    host: str = field(default_factory=lambda: _env("NEXUS_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("NEXUS_PORT", 8766))
    mcp_path: str = field(default_factory=lambda: _env("NEXUS_MCP_PATH", "/mcp"))
    log_level: str = field(default_factory=lambda: _env("NEXUS_LOG_LEVEL", "info"))
    max_concurrent: int = field(default_factory=lambda: _env_int("NEXUS_MAX_CONCURRENT", 8))
    workers: int = field(default_factory=lambda: _env_int("NEXUS_WORKERS", 2))

    # Database (optional)
    db_host: str = field(default_factory=lambda: _env("NEXUS_DB_HOST", ""))
    db_port: int = field(default_factory=lambda: _env_int("NEXUS_DB_PORT", 5432))
    db_name: str = field(default_factory=lambda: _env("NEXUS_DB_NAME", ""))
    db_user: str = field(default_factory=lambda: _env("NEXUS_DB_USER", ""))
    db_password: str = field(default_factory=lambda: _env("NEXUS_DB_PASSWORD", ""))

    # Rate limiting
    rate_limit_rpm: int = field(default_factory=lambda: _env_int("NEXUS_RATE_LIMIT_RPM", 120))
    rate_limit_burst: int = field(default_factory=lambda: _env_int("NEXUS_RATE_LIMIT_BURST", 20))

    # Health & Recovery
    watchdog_interval: int = field(default_factory=lambda: _env_int("NEXUS_WATCHDOG_INTERVAL", 30))
    watchdog_services: list[str] = field(default_factory=lambda: _env_list("NEXUS_WATCHDOG_SERVICES", ""))
    max_restart_attempts: int = field(default_factory=lambda: _env_int("NEXUS_MAX_RESTART_ATTEMPTS", 10))
    restart_cooldown: int = field(default_factory=lambda: _env_int("NEXUS_RESTART_COOLDOWN", 120))

    # Intelligence
    intelligence_enabled: bool = field(default_factory=lambda: _env_bool("NEXUS_INTELLIGENCE", True))
    data_dir: str = field(default_factory=lambda: _env("NEXUS_DATA_DIR", "~/.mcp-nexus"))

    @property
    def is_localhost(self) -> bool:
        """Detect if the target server is the same machine (skip SSH)."""
        host = self.ssh_host
        if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            return True
        try:
            local_ips = {
                addr[4][0]
                for info in socket.getaddrinfo(socket.gethostname(), None)
                for addr in [info]
            }
            local_ips.add("127.0.0.1")
            return host in local_ips
        except Exception:
            return False

    @property
    def db_dsn(self) -> str:
        if not self.db_host:
            return ""
        return f"postgresql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"
