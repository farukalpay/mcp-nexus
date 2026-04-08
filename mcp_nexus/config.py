"""Configuration management with environment variable support."""

from __future__ import annotations

import json
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


def _join_url(base: str, path: str) -> str:
    normalized_base = base.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    if not normalized_base:
        return ""
    return f"{normalized_base}{normalized_path}"


@dataclass(frozen=True)
class DatabaseProfile:
    """Named PostgreSQL connection profile resolved from the environment."""

    name: str
    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str = ""

    @property
    def dsn(self) -> str:
        base = f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"
        if self.sslmode:
            return f"{base}?sslmode={self.sslmode}"
        return base

    def redacted(self) -> dict[str, object]:
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "sslmode": self.sslmode or None,
            "has_password": bool(self.password),
        }


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
    oauth_client_redirect_uris: list[str] = field(
        default_factory=lambda: _env_list("NEXUS_OAUTH_CLIENT_REDIRECT_URIS", "")
    )
    oauth_issuer: str = field(default_factory=lambda: _env("NEXUS_OAUTH_ISSUER", ""))
    oauth_enabled: bool = field(default_factory=lambda: _env_bool("NEXUS_OAUTH_ENABLED", True))
    public_base_url: str = field(default_factory=lambda: _env("NEXUS_PUBLIC_BASE_URL", ""))
    oauth_scopes: list[str] = field(default_factory=lambda: _env_list("NEXUS_OAUTH_SCOPES", "nexus"))
    oauth_default_scopes: list[str] = field(default_factory=lambda: _env_list("NEXUS_OAUTH_DEFAULT_SCOPES", "nexus"))
    oauth_token_ttl_seconds: int = field(default_factory=lambda: _env_int("NEXUS_OAUTH_TOKEN_TTL_SECONDS", 3600))
    oauth_refresh_ttl_seconds: int = field(
        default_factory=lambda: _env_int("NEXUS_OAUTH_REFRESH_TTL_SECONDS", 2592000)
    )
    oauth_authorization_code_ttl_seconds: int = field(
        default_factory=lambda: _env_int("NEXUS_OAUTH_AUTHORIZATION_CODE_TTL_SECONDS", 600)
    )
    oauth_consent_path: str = field(default_factory=lambda: _env("NEXUS_OAUTH_CONSENT_PATH", "/oauth/consent"))

    # Server
    host: str = field(default_factory=lambda: _env("NEXUS_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("NEXUS_PORT", 8766))
    mcp_path: str = field(default_factory=lambda: _env("NEXUS_MCP_PATH", "/mcp"))
    mcp_path_aliases: list[str] = field(default_factory=lambda: _env_list("NEXUS_MCP_PATH_ALIASES", ""))
    log_level: str = field(default_factory=lambda: _env("NEXUS_LOG_LEVEL", "info"))
    max_concurrent: int = field(default_factory=lambda: _env_int("NEXUS_MAX_CONCURRENT", 8))
    workers: int = field(default_factory=lambda: _env_int("NEXUS_WORKERS", 2))
    default_cwd: str = field(default_factory=lambda: _env("NEXUS_DEFAULT_CWD", ""))
    default_command_timeout: int = field(default_factory=lambda: _env_int("NEXUS_COMMAND_TIMEOUT", 60))
    output_limit_bytes: int = field(default_factory=lambda: _env_int("NEXUS_OUTPUT_LIMIT_BYTES", 50000))
    error_limit_bytes: int = field(default_factory=lambda: _env_int("NEXUS_ERROR_LIMIT_BYTES", 10000))
    output_preview_bytes: int = field(default_factory=lambda: _env_int("NEXUS_OUTPUT_PREVIEW_BYTES", 4000))
    error_preview_bytes: int = field(default_factory=lambda: _env_int("NEXUS_ERROR_PREVIEW_BYTES", 2000))
    sandbox_root: str = field(default_factory=lambda: _env("NEXUS_SANDBOX_ROOT", "~/.mcp-nexus/sandboxes"))
    release_root: str = field(default_factory=lambda: _env("NEXUS_RELEASE_ROOT", "/opt/mcp-nexus/releases"))
    current_release_link: str = field(
        default_factory=lambda: _env("NEXUS_CURRENT_RELEASE_LINK", "/opt/mcp-nexus/current")
    )
    audit_log_file: str = field(default_factory=lambda: _env("NEXUS_AUDIT_LOG_FILE", ""))
    artifact_root: str = field(default_factory=lambda: _env("NEXUS_ARTIFACT_ROOT", "~/.mcp-nexus/artifacts"))
    job_root: str = field(default_factory=lambda: _env("NEXUS_JOB_ROOT", "/var/tmp/mcp-nexus/jobs"))
    tool_alias_base: str = field(default_factory=lambda: _env("NEXUS_TOOL_ALIAS_BASE", "/mcp-nexus"))
    forwarded_headers: list[str] = field(
        default_factory=lambda: _env_list(
            "NEXUS_FORWARDED_HEADERS",
            "forwarded,x-forwarded-for,x-forwarded-host,x-forwarded-port,x-forwarded-proto",
        )
    )

    # Database (optional)
    db_host: str = field(default_factory=lambda: _env("NEXUS_DB_HOST", ""))
    db_port: int = field(default_factory=lambda: _env_int("NEXUS_DB_PORT", 5432))
    db_name: str = field(default_factory=lambda: _env("NEXUS_DB_NAME", ""))
    db_user: str = field(default_factory=lambda: _env("NEXUS_DB_USER", ""))
    db_password: str = field(default_factory=lambda: _env("NEXUS_DB_PASSWORD", ""))
    db_sslmode: str = field(default_factory=lambda: _env("NEXUS_DB_SSLMODE", ""))
    db_default_profile: str = field(default_factory=lambda: _env("NEXUS_DB_DEFAULT_PROFILE", "default"))
    db_profiles_json: str = field(default_factory=lambda: _env("NEXUS_DB_PROFILES_JSON", ""))

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
            local_ips = {addr[4][0] for info in socket.getaddrinfo(socket.gethostname(), None) for addr in [info]}
            local_ips.add("127.0.0.1")
            return host in local_ips
        except Exception:
            return False

    @property
    def db_dsn(self) -> str:
        profile = self.resolve_db_profile()
        return profile.dsn if profile else ""

    @property
    def oauth_issuer_url(self) -> str:
        issuer = self.oauth_issuer or self.public_base_url
        return issuer.rstrip("/")

    @property
    def oauth_resource_server_url(self) -> str:
        if not self.public_base_url:
            return ""
        return _join_url(self.public_base_url, self.mcp_path)

    @property
    def oauth_consent_url(self) -> str:
        return _join_url(self.oauth_issuer_url, self.oauth_consent_path)

    @property
    def oauth_service_documentation_url(self) -> str:
        if not self.public_base_url:
            return ""
        return _join_url(self.public_base_url, "/info/nexus")

    @property
    def oauth_valid_scopes(self) -> list[str]:
        return self.oauth_scopes or self.oauth_default_scopes or ["nexus"]

    @property
    def oauth_required_scopes(self) -> list[str]:
        return self.oauth_default_scopes or self.oauth_valid_scopes or ["nexus"]

    @property
    def oauth_ready(self) -> bool:
        return self.oauth_enabled and bool(self.oauth_issuer_url and self.oauth_resource_server_url)

    @property
    def oauth_static_client_enabled(self) -> bool:
        return bool(self.oauth_enabled and self.oauth_client_id and self.oauth_client_redirect_uris)

    def expanded_path(self, value: str) -> str:
        return str(Path(value).expanduser()) if value else value

    def database_profiles(self) -> dict[str, DatabaseProfile]:
        profiles: dict[str, DatabaseProfile] = {}

        if self.db_profiles_json:
            raw_profiles = json.loads(self.db_profiles_json)
            if not isinstance(raw_profiles, dict):
                raise ValueError("NEXUS_DB_PROFILES_JSON must be a JSON object keyed by profile name")
            for name, payload in raw_profiles.items():
                if not isinstance(payload, dict):
                    raise ValueError(f"Database profile {name!r} must be a JSON object")
                profiles[name] = DatabaseProfile(
                    name=name,
                    host=str(payload.get("host", "")),
                    port=int(payload.get("port", 5432)),
                    database=str(payload.get("database") or payload.get("dbname") or payload.get("name") or ""),
                    user=str(payload.get("user", "")),
                    password=str(payload.get("password", "")),
                    sslmode=str(payload.get("sslmode", "")),
                )

        if self.db_host:
            legacy_name = self.db_default_profile or ("default" if not profiles else "legacy")
            profiles.setdefault(
                legacy_name,
                DatabaseProfile(
                    name=legacy_name,
                    host=self.db_host,
                    port=self.db_port,
                    database=self.db_name,
                    user=self.db_user,
                    password=self.db_password,
                    sslmode=self.db_sslmode,
                ),
            )

        return {
            name: profile for name, profile in profiles.items() if profile.host and profile.database and profile.user
        }

    def resolve_db_profile(self, name: str = "") -> DatabaseProfile | None:
        profiles = self.database_profiles()
        if not profiles:
            return None
        if name:
            return profiles.get(name)
        if self.db_default_profile and self.db_default_profile in profiles:
            return profiles[self.db_default_profile]
        return next(iter(profiles.values()))
