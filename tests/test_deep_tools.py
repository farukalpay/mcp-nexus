"""Tests for the newer deep tool helpers."""

from __future__ import annotations

import pytest

from mcp_nexus.catalog import catalog_summary, category_for_tool
from mcp_nexus.tools.database import (
    DEFAULT_DB_CLIENT_MODULES,
    DEFAULT_DB_CLIENT_PACKAGES,
    _normalize_db_client_modules,
    _normalize_db_client_packages,
    _resolve_sql_text,
)
from mcp_nexus.tools.filesystem import _parse_compare_status_output
from mcp_nexus.tools.git import _parse_status_output
from mcp_nexus.tools.terminal import (
    BatchCommandResult,
    _aggregate_batch_usage,
    _argv_command,
    _safe_cwd,
    _stdin_script_command,
)


def test_aggregate_batch_usage_sums_resource_metrics() -> None:
    results = [
        BatchCommandResult(
            index=1,
            command="echo one",
            ok=True,
            exit_code=0,
            duration_ms=10.5,
            error_code=None,
            error_stage=None,
            stdout_preview="one",
            stderr_preview="",
            usage={"wall_ms": 10.5, "user_cpu_s": 1.1, "system_cpu_s": 0.2, "max_rss_kb": 100},
        ),
        BatchCommandResult(
            index=2,
            command="echo two",
            ok=False,
            exit_code=1,
            duration_ms=20.25,
            error_code="COMMAND_FAILED",
            error_stage="execution",
            stdout_preview="",
            stderr_preview="boom",
            usage={"wall_ms": 20.25, "user_cpu_s": 2.4, "system_cpu_s": 0.3, "max_rss_kb": 250},
        ),
    ]

    usage = _aggregate_batch_usage(results)

    assert usage is not None
    assert usage["command_count"] == 2
    assert usage["usage_count"] == 2
    assert usage["wall_ms_total"] == pytest.approx(30.75)
    assert usage["user_cpu_s_total"] == pytest.approx(3.5)
    assert usage["system_cpu_s_total"] == pytest.approx(0.5)
    assert usage["max_rss_kb_peak"] == 250


def test_parse_git_status_output_reports_branch_and_change_counts() -> None:
    parsed = _parse_status_output(
        "\n".join(
            [
                "## main...origin/main [ahead 2, behind 1]",
                " M modified.py",
                "A  staged.py",
                "?? new.txt",
                "UU conflict.py",
            ]
        ),
        max_entries=10,
    )

    assert parsed["header"]["branch"] == "main"
    assert parsed["header"]["upstream"] == "origin/main"
    assert parsed["header"]["ahead"] == 2
    assert parsed["header"]["behind"] == 1
    assert parsed["counts"]["staged"] == 2
    assert parsed["counts"]["unstaged"] == 2
    assert parsed["counts"]["untracked"] == 1
    assert parsed["counts"]["conflicted"] == 1
    assert parsed["counts"]["tracked"] == 3
    assert parsed["counts"]["total"] == 4
    assert parsed["dirty"] is True
    assert parsed["truncated"] is False


def test_parse_compare_status_output_tracks_renames_and_counts() -> None:
    parsed = _parse_compare_status_output(
        "\n".join(
            [
                "M\tapp.py",
                "A\tnew.py",
                "D\told.py",
                "R100\tfrom.txt\tto.txt",
            ]
        ),
        max_entries=10,
    )

    assert parsed["counts"]["modified"] == 1
    assert parsed["counts"]["added"] == 1
    assert parsed["counts"]["deleted"] == 1
    assert parsed["counts"]["renamed"] == 1
    assert parsed["total"] == 4
    assert parsed["changes"][3]["old_path"] == "from.txt"
    assert parsed["changes"][3]["new_path"] == "to.txt"


def test_catalog_includes_new_deep_tools() -> None:
    assert category_for_tool("execute_batch") == "terminal"
    assert category_for_tool("execute_python_file") == "terminal"
    assert category_for_tool("git_diagnose") == "git"
    assert category_for_tool("compare_paths") == "filesystem"
    assert category_for_tool("db_client_status") == "database"
    assert category_for_tool("db_client_bootstrap") == "database"
    assert category_for_tool("inspect_database") == "database"
    assert category_for_tool("db_table_inspect") == "database"
    assert category_for_tool("tabular_dataset_profile") == "analysis"
    assert category_for_tool("train_tabular_classifier") == "analysis"

    summary = catalog_summary()
    assert summary.total_tools == 163
    assert summary.category_counts["terminal"] == 11
    assert summary.category_counts["git"] == 15
    assert summary.category_counts["filesystem"] == 20
    assert summary.category_counts["database"] == 22
    assert summary.category_counts["analysis"] == 2


def test_db_client_defaults_are_minimal_and_deduplicated() -> None:
    assert tuple(_normalize_db_client_modules(None)) == DEFAULT_DB_CLIENT_MODULES
    assert tuple(_normalize_db_client_packages(None)) == DEFAULT_DB_CLIENT_PACKAGES
    assert _normalize_db_client_modules([" psycopg ", "", "sqlalchemy", "psycopg"]) == ["psycopg", "sqlalchemy"]
    assert _normalize_db_client_packages([" psycopg[binary] ", "psycopg[binary]", "sqlalchemy"]) == [
        "psycopg[binary]",
        "sqlalchemy",
    ]


def test_resolve_sql_text_accepts_query_or_sql_alias() -> None:
    assert _resolve_sql_text(query="SELECT 1", sql="") == "SELECT 1"
    assert _resolve_sql_text(query="", sql="SELECT 2") == "SELECT 2"
    with pytest.raises(ValueError, match="Provide either query or sql"):
        _resolve_sql_text(query="SELECT 1", sql="SELECT 2")


def test_safe_cwd_expands_user_paths(monkeypatch) -> None:
    class StubSettings:
        default_cwd = "~/workspace"

        @staticmethod
        def expanded_path(value: str) -> str:
            return value.replace("~", "/home/tester", 1)

    monkeypatch.setattr("mcp_nexus.tools.terminal.get_settings", lambda: StubSettings())

    assert _safe_cwd("") == "/home/tester/workspace"
    assert _safe_cwd("~/repo") == "/home/tester/repo"


def test_stdin_script_command_avoids_external_tempfile_utilities() -> None:
    command = _stdin_script_command("python3", "print('hello')", stdin_flag="-")

    assert command.startswith("python3 - <<'NEXUS_SCRIPT_EOF'")
    assert "cat >" not in command
    assert "rm -f" not in command


def test_argv_command_quotes_arguments() -> None:
    command = _argv_command("python3", ["/tmp/my script.py", "--label=error case"])

    assert command == "python3 '/tmp/my script.py' '--label=error case'"
