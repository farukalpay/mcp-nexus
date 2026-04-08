"""Tests for configuration module."""

import json

from mcp_nexus.config import Settings


def test_default_settings():
    s = Settings()
    assert s.port == 8766 or isinstance(s.port, int)
    assert s.mcp_path.startswith("/mcp")
    assert s.ssh_port == 22 or isinstance(s.ssh_port, int)


def test_localhost_detection():
    s = Settings()
    s.ssh_host = "127.0.0.1"
    assert s.is_localhost is True

    s.ssh_host = "localhost"
    assert s.is_localhost is True

    s.ssh_host = "8.8.8.8"
    assert s.is_localhost is False


def test_db_dsn():
    s = Settings()
    s.db_host = ""
    assert s.db_dsn == ""

    s.db_host = "localhost"
    s.db_port = 5432
    s.db_name = "test"
    s.db_user = "user"
    s.db_password = "pass"
    assert "postgresql://user:pass@localhost:5432/test" == s.db_dsn


def test_database_profiles_from_json(monkeypatch):
    monkeypatch.setenv(
        "NEXUS_DB_PROFILES_JSON",
        json.dumps(
            {
                "warehouse": {
                    "host": "db.internal",
                    "port": 5433,
                    "database": "analytics",
                    "user": "readonly",
                    "password": "secret",
                }
            }
        ),
    )
    monkeypatch.setenv("NEXUS_DB_DEFAULT_PROFILE", "warehouse")

    settings = Settings()
    profile = settings.resolve_db_profile()
    assert profile is not None
    assert profile.name == "warehouse"
    assert profile.host == "db.internal"
    assert profile.database == "analytics"


def test_database_profiles_legacy_fallback(monkeypatch):
    monkeypatch.delenv("NEXUS_DB_PROFILES_JSON", raising=False)
    monkeypatch.setenv("NEXUS_DB_HOST", "localhost")
    monkeypatch.setenv("NEXUS_DB_PORT", "5432")
    monkeypatch.setenv("NEXUS_DB_NAME", "legacy")
    monkeypatch.setenv("NEXUS_DB_USER", "postgres")
    monkeypatch.setenv("NEXUS_DB_PASSWORD", "pw")
    monkeypatch.setenv("NEXUS_DB_DEFAULT_PROFILE", "default")

    settings = Settings()
    profiles = settings.database_profiles()
    assert "default" in profiles
    assert profiles["default"].database == "legacy"
