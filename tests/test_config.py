"""Tests for configuration module."""

import os
import pytest
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
