"""Structured PostgreSQL tools with stable profiles and safe identifier handling."""

from __future__ import annotations

import csv
import io
import shlex
import time
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_nexus.config import DatabaseProfile
from mcp_nexus.results import ToolResult, build_tool_result
from mcp_nexus.runtime import primary_package_manager
from mcp_nexus.server import get_artifacts, get_pool, get_session_store, get_settings, tool_context
from mcp_nexus.telemetry import get_request_trace
from mcp_nexus.transport.ssh import CommandResult


@dataclass(frozen=True)
class DatabaseRuntime:
    profile: DatabaseProfile
    package_manager: str
    capabilities: dict[str, Any]


def _install_psql_snippet(manager: str) -> str:
    installers = {
        "apt-get": "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-client",
        "dnf": "dnf install -y -q postgresql",
        "yum": "yum install -y -q postgresql",
        "apk": "apk add --no-cache postgresql-client",
        "pacman": "pacman -S --noconfirm postgresql",
        "zypper": "zypper install -y postgresql",
        "brew": 'brew install libpq && export PATH="$(brew --prefix libpq)/bin:$PATH"',
    }
    installer = installers.get(manager)
    if not installer:
        return "echo 'psql missing and no supported package manager is available' >&2; exit 1"
    return (
        "if ! command -v psql >/dev/null 2>&1; then "
        f"{installer}; "
        "fi; "
        "if ! command -v psql >/dev/null 2>&1; then "
        "echo 'psql is still unavailable after installation attempt' >&2; exit 1; "
        "fi; "
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _parse_relation_name(name: str) -> tuple[str, str]:
    raw = name.strip()
    if not raw:
        raise ValueError("table name is required")

    parts: list[str] = []
    current: list[str] = []
    in_quotes = False
    for char in raw:
        if char == '"':
            in_quotes = not in_quotes
            current.append(char)
            continue
        if char == "." and not in_quotes:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())

    if len(parts) == 1:
        schema_name, table_name = "public", parts[0]
    elif len(parts) == 2:
        schema_name, table_name = parts
    else:
        raise ValueError("table name must be table or schema.table")

    return _strip_identifier_quotes(schema_name), _strip_identifier_quotes(table_name)


def _strip_identifier_quotes(value: str) -> str:
    candidate = value.strip()
    if len(candidate) >= 2 and candidate.startswith('"') and candidate.endswith('"'):
        candidate = candidate[1:-1].replace('""', '"')
    return candidate


def _qualified_relation(schema_name: str, table_name: str) -> str:
    return f"{_quote_identifier(schema_name)}.{_quote_identifier(table_name)}"


def _normalize_query(query: str) -> str:
    return query.strip().rstrip(";")


def _apply_row_limit(query: str, max_rows: int) -> tuple[str, bool]:
    normalized = _normalize_query(query)
    if max_rows <= 0:
        return normalized, False
    lowered = normalized.lower()
    if lowered.startswith(("select ", "with ")) and " limit " not in lowered:
        return f"{normalized} LIMIT {max_rows}", True
    return normalized, False


def _parse_csv_rows(stdout: str) -> list[dict[str, Any]]:
    if not stdout.strip():
        return []
    reader = csv.DictReader(io.StringIO(stdout))
    return [dict(row) for row in reader]


def _coerce_row_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settings = get_settings()
    if len(rows) <= 20 and len(str(rows)) <= settings.output_limit_bytes:
        return {"rows": rows, "row_count": len(rows), "rows_truncated": False}
    return {"rows_preview": rows[:20], "row_count": len(rows), "rows_truncated": True}


def _db_result(
    tool_name: str,
    *,
    ok: bool,
    duration_ms: float,
    profile_name: str | None = None,
    stdout_text: str = "",
    stderr_text: str = "",
    error_code: str | None = None,
    error_stage: str | None = None,
    message: str | None = None,
    exit_code: int | None = None,
    data: Any = None,
    extra_artifacts=None,
) -> ToolResult:
    settings = get_settings()
    return build_tool_result(
        context=tool_context(tool_name),
        artifacts=get_artifacts(),
        ok=ok,
        duration_ms=duration_ms,
        stdout_text=stdout_text,
        stderr_text=stderr_text,
        output_limit=settings.output_limit_bytes,
        error_limit=settings.error_limit_bytes,
        output_preview_limit=settings.output_preview_bytes,
        error_preview_limit=settings.error_preview_bytes,
        error_code=error_code,
        error_stage=error_stage,
        message=message,
        exit_code=exit_code,
        data=data,
        profile=profile_name,
        extra_artifacts=extra_artifacts,
    )


def _db_error(result: CommandResult) -> tuple[str | None, str | None, str | None]:
    if result.ok:
        return None, None, None
    stderr = result.stderr.lower()
    if result.exit_code == 124 or "timed out" in stderr:
        return "DB_TIMEOUT", "query", "Database command timed out."
    return "DB_QUERY_FAILED", "query", "Database command failed."


def _profile_name_for_request(explicit_name: str = "") -> str:
    if explicit_name:
        return explicit_name
    trace = get_request_trace()
    if trace and trace.session_id:
        active = get_session_store().get_active_db_profile(trace.session_id)
        if active:
            return active
    profile = get_settings().resolve_db_profile()
    return profile.name if profile else ""


async def _database_runtime(profile_name: str = "") -> DatabaseRuntime | None:
    settings = get_settings()
    selected_name = _profile_name_for_request(profile_name)
    profile = settings.resolve_db_profile(selected_name)
    if profile is None:
        return None

    pool = get_pool()
    conn = await pool.acquire()
    try:
        capabilities = await conn.probe_capabilities()
    finally:
        pool.release(conn)

    return DatabaseRuntime(
        profile=profile,
        package_manager=primary_package_manager(capabilities),
        capabilities=capabilities.to_dict(),
    )


def _psql_command(query: str, runtime: DatabaseRuntime, *, csv_output: bool = False) -> str:
    install_snippet = _install_psql_snippet(runtime.package_manager)
    env_parts = []
    if runtime.profile.password:
        env_parts.append(f"PGPASSWORD={shlex.quote(runtime.profile.password)}")
    if runtime.profile.sslmode:
        env_parts.append(f"PGSSLMODE={shlex.quote(runtime.profile.sslmode)}")

    args = [
        "psql",
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-q",
        "-P",
        "footer=off",
        "-h",
        shlex.quote(runtime.profile.host),
        "-p",
        str(runtime.profile.port),
        "-U",
        shlex.quote(runtime.profile.user),
        "-d",
        shlex.quote(runtime.profile.database),
    ]
    if csv_output:
        args.append("--csv")
    else:
        args.extend(["-t", "-A"])
    args.extend(["-c", shlex.quote(query)])
    prefix = " ".join(env_parts) + (" " if env_parts else "")
    return install_snippet + prefix + " ".join(args)


async def _run_sql(
    query: str,
    *,
    runtime: DatabaseRuntime,
    timeout: int,
    csv_output: bool = False,
) -> tuple[CommandResult, float]:
    pool = get_pool()
    conn = await pool.acquire()
    try:
        started = time.monotonic()
        result = await conn.run_full(_psql_command(query, runtime, csv_output=csv_output), timeout=timeout)
        return result, (time.monotonic() - started) * 1000
    finally:
        pool.release(conn)


def _config_error(tool_name: str, *, started: float, profile_name: str = "") -> ToolResult:
    return _db_result(
        tool_name,
        ok=False,
        duration_ms=(time.monotonic() - started) * 1000,
        profile_name=profile_name or None,
        error_code="DB_PROFILE_NOT_CONFIGURED",
        error_stage="configuration",
        message="No database profile is configured. Set NEXUS_DB_PROFILES_JSON or legacy NEXUS_DB_* values.",
    )


def _relation_filter(schema_name: str, table_name: str) -> str:
    return f"table_schema = {_sql_literal(schema_name)} AND table_name = {_sql_literal(table_name)}"


async def _query_rows(
    *,
    query: str,
    runtime: DatabaseRuntime,
    timeout: int = 30,
) -> tuple[CommandResult, list[dict[str, Any]], float]:
    result, duration_ms = await _run_sql(query, runtime=runtime, timeout=timeout, csv_output=True)
    rows = _parse_csv_rows(result.stdout) if result.ok else []
    return result, rows, duration_ms


def register(mcp: FastMCP):

    @mcp.tool(structured_output=True)
    async def db_profiles() -> ToolResult:
        """List configured database profiles without exposing secrets."""
        started = time.monotonic()
        settings = get_settings()
        profiles = settings.database_profiles()
        active_profile = _profile_name_for_request()
        return _db_result(
            "db_profiles",
            ok=True,
            duration_ms=(time.monotonic() - started) * 1000,
            profile_name=active_profile or None,
            data={
                "profiles": [
                    {**profile.redacted(), "active": profile.name == active_profile} for profile in profiles.values()
                ],
                "active_profile": active_profile or None,
                "default_profile": settings.db_default_profile or None,
            },
        )

    @mcp.tool(structured_output=True)
    async def db_use(profile_name: str) -> ToolResult:
        """Bind the current MCP session to a named database profile."""
        started = time.monotonic()
        profile = get_settings().resolve_db_profile(profile_name)
        if profile is None:
            return _db_result(
                "db_use",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                profile_name=profile_name,
                error_code="DB_PROFILE_NOT_FOUND",
                error_stage="configuration",
                message=f"Database profile {profile_name!r} was not found.",
            )

        trace = get_request_trace()
        if trace is None or trace.session_id is None:
            return _db_result(
                "db_use",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                profile_name=profile_name,
                error_code="SESSION_REQUIRED",
                error_stage="session_binding",
                message="db_use requires a stateful MCP session so the active profile can persist across calls.",
                data={"profile": profile.redacted()},
            )

        get_session_store().set_active_db_profile(trace.session_id, profile_name)
        return _db_result(
            "db_use",
            ok=True,
            duration_ms=(time.monotonic() - started) * 1000,
            profile_name=profile_name,
            data={"profile": profile.redacted(), "session_id": trace.session_id, "active": True},
        )

    @mcp.tool(structured_output=True)
    async def db_query(query: str, max_rows: int = 100, profile: str = "") -> ToolResult:
        """Execute a SQL query and return structured rows when the result is reasonably small."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_query", started=started, profile_name=profile)

        effective_query, limit_applied = _apply_row_limit(query, max_rows)
        result, rows, duration_ms = await _query_rows(query=effective_query, runtime=runtime, timeout=30)
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_query",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={
                "query": query,
                "effective_query": effective_query,
                "row_limit_applied": limit_applied,
                "profile": runtime.profile.redacted(),
                "capabilities": runtime.capabilities,
                **_coerce_row_payload(rows),
            },
        )

    @mcp.tool(structured_output=True)
    async def db_safe_query(query: str, max_rows: int = 100, profile: str = "") -> ToolResult:
        """Execute a query inside a read-only transaction enforced by PostgreSQL."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_safe_query", started=started, profile_name=profile)

        effective_query, limit_applied = _apply_row_limit(query, max_rows)
        wrapped_query = f"BEGIN TRANSACTION READ ONLY; {effective_query}; ROLLBACK;"
        result, rows, duration_ms = await _query_rows(query=wrapped_query, runtime=runtime, timeout=30)
        error_code, error_stage, message = _db_error(result)
        if not result.ok and "read-only transaction" in result.stderr.lower():
            error_code = "DB_READ_ONLY_VIOLATION"
            message = "Query attempted to write inside a read-only transaction."
        return _db_result(
            "db_safe_query",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={
                "query": query,
                "effective_query": effective_query,
                "row_limit_applied": limit_applied,
                "profile": runtime.profile.redacted(),
                **_coerce_row_payload(rows),
            },
        )

    @mcp.tool(structured_output=True)
    async def db_tables(profile: str = "") -> ToolResult:
        """List tables and relation sizes for the active database profile."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_tables", started=started, profile_name=profile)

        query = (
            "SELECT table_schema, table_name, "
            "pg_size_pretty(pg_total_relation_size(format('%I.%I', table_schema, table_name)::regclass)) AS total_size "
            "FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY table_schema, table_name"
        )
        result, rows, duration_ms = await _query_rows(query=query, runtime=runtime, timeout=30)
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_tables",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={"tables": rows, "table_count": len(rows), "profile": runtime.profile.redacted()},
        )

    @mcp.tool(structured_output=True)
    async def db_schema(table_name: str, profile: str = "") -> ToolResult:
        """Inspect a table schema using explicit schema/table resolution and safe identifier quoting."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_schema", started=started, profile_name=profile)

        try:
            schema_name, relation_name = _parse_relation_name(table_name)
        except ValueError as exc:
            return _db_result(
                "db_schema",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                profile_name=runtime.profile.name,
                error_code="INVALID_RELATION_NAME",
                error_stage="validation",
                message=str(exc),
            )

        query = (
            "SELECT column_name, data_type, udt_name, is_nullable, column_default "
            "FROM information_schema.columns "
            f"WHERE {_relation_filter(schema_name, relation_name)} "
            "ORDER BY ordinal_position"
        )
        result, rows, duration_ms = await _query_rows(query=query, runtime=runtime, timeout=20)
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_schema",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={
                "schema": schema_name,
                "table": relation_name,
                "qualified_name": _qualified_relation(schema_name, relation_name),
                "columns": rows,
            },
        )

    @mcp.tool(structured_output=True)
    async def db_sample(table: str, limit: int = 20, profile: str = "") -> ToolResult:
        """Return a small sample of rows from a table."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_sample", started=started, profile_name=profile)

        try:
            schema_name, relation_name = _parse_relation_name(table)
        except ValueError as exc:
            return _db_result(
                "db_sample",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                profile_name=runtime.profile.name,
                error_code="INVALID_RELATION_NAME",
                error_stage="validation",
                message=str(exc),
            )

        query = f"SELECT * FROM {_qualified_relation(schema_name, relation_name)} LIMIT {max(1, min(limit, 1000))}"
        result, rows, duration_ms = await _query_rows(query=query, runtime=runtime, timeout=30)
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_sample",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={"schema": schema_name, "table": relation_name, "limit": limit, "rows": rows, "row_count": len(rows)},
        )

    @mcp.tool(structured_output=True)
    async def db_profile(table: str, profile: str = "") -> ToolResult:
        """Profile a table using planner statistics plus temporal min/max inspection."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_profile", started=started, profile_name=profile)

        try:
            schema_name, relation_name = _parse_relation_name(table)
        except ValueError as exc:
            return _db_result(
                "db_profile",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                profile_name=runtime.profile.name,
                error_code="INVALID_RELATION_NAME",
                error_stage="validation",
                message=str(exc),
            )

        schema_query = (
            "SELECT column_name, data_type, udt_name, is_nullable "
            "FROM information_schema.columns "
            f"WHERE {_relation_filter(schema_name, relation_name)} "
            "ORDER BY ordinal_position"
        )
        stats_query = (
            "SELECT attname AS column_name, null_frac, n_distinct, "
            "coalesce(most_common_vals::text, '') AS most_common_vals, "
            "coalesce(most_common_freqs::text, '') AS most_common_freqs "
            "FROM pg_stats "
            f"WHERE schemaname = {_sql_literal(schema_name)} AND tablename = {_sql_literal(relation_name)} "
            "ORDER BY attname"
        )
        relation_query = (
            "SELECT c.reltuples::bigint AS row_estimate, "
            "pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size "
            "FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE n.nspname = {_sql_literal(schema_name)} AND c.relname = {_sql_literal(relation_name)}"
        )

        schema_result, schema_rows, schema_ms = await _query_rows(query=schema_query, runtime=runtime, timeout=20)
        if not schema_result.ok:
            error_code, error_stage, message = _db_error(schema_result)
            return _db_result(
                "db_profile",
                ok=False,
                duration_ms=schema_ms,
                profile_name=runtime.profile.name,
                stdout_text=schema_result.stdout,
                stderr_text=schema_result.stderr,
                error_code=error_code,
                error_stage=error_stage,
                message=message,
                exit_code=schema_result.exit_code,
            )

        stats_result, stats_rows, stats_ms = await _query_rows(query=stats_query, runtime=runtime, timeout=20)
        relation_result, relation_rows, relation_ms = await _query_rows(
            query=relation_query, runtime=runtime, timeout=20
        )
        for partial_result, partial_ms in (
            (stats_result, stats_ms),
            (relation_result, relation_ms),
        ):
            if not partial_result.ok:
                error_code, error_stage, message = _db_error(partial_result)
                return _db_result(
                    "db_profile",
                    ok=False,
                    duration_ms=schema_ms + partial_ms,
                    profile_name=runtime.profile.name,
                    stdout_text=partial_result.stdout,
                    stderr_text=partial_result.stderr,
                    error_code=error_code,
                    error_stage=error_stage,
                    message=message,
                    exit_code=partial_result.exit_code,
                )

        temporal_columns = [
            row["column_name"]
            for row in schema_rows
            if row.get("data_type") in {"timestamp without time zone", "timestamp with time zone", "date"}
        ]
        temporal_bounds = {}
        temporal_stdout = ""
        temporal_stderr = ""
        temporal_ms = 0.0
        if temporal_columns:
            select_parts = []
            for column_name in temporal_columns:
                identifier = _quote_identifier(column_name)
                select_parts.append(f"min({identifier}) AS {_quote_identifier(column_name + '_min')}")
                select_parts.append(f"max({identifier}) AS {_quote_identifier(column_name + '_max')}")
            temporal_query = f"SELECT {', '.join(select_parts)} FROM {_qualified_relation(schema_name, relation_name)}"
            temporal_result, temporal_rows, temporal_ms = await _query_rows(
                query=temporal_query,
                runtime=runtime,
                timeout=30,
            )
            temporal_stdout = temporal_result.stdout
            temporal_stderr = temporal_result.stderr
            if not temporal_result.ok:
                error_code, error_stage, message = _db_error(temporal_result)
                return _db_result(
                    "db_profile",
                    ok=False,
                    duration_ms=schema_ms + stats_ms + relation_ms + temporal_ms,
                    profile_name=runtime.profile.name,
                    stdout_text=temporal_result.stdout,
                    stderr_text=temporal_result.stderr,
                    error_code=error_code,
                    error_stage=error_stage,
                    message=message,
                    exit_code=temporal_result.exit_code,
                )
            if temporal_result.ok and temporal_rows:
                temporal_bounds = temporal_rows[0]

        stats_by_column = {row["column_name"]: row for row in stats_rows}
        columns = []
        for row in schema_rows:
            stats = stats_by_column.get(row["column_name"], {})
            columns.append(
                {
                    "column_name": row["column_name"],
                    "data_type": row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                    "null_ratio": stats.get("null_frac"),
                    "distinct_estimate": stats.get("n_distinct"),
                    "target_distribution": {
                        "most_common_values": stats.get("most_common_vals"),
                        "most_common_frequencies": stats.get("most_common_freqs"),
                    },
                    "min": temporal_bounds.get(f"{row['column_name']}_min"),
                    "max": temporal_bounds.get(f"{row['column_name']}_max"),
                }
            )

        total_duration = schema_ms + stats_ms + relation_ms + temporal_ms
        stdout_text = "\n".join(
            part
            for part in (schema_result.stdout, stats_result.stdout, relation_result.stdout, temporal_stdout)
            if part
        )
        stderr_text = "\n".join(
            part
            for part in (schema_result.stderr, stats_result.stderr, relation_result.stderr, temporal_stderr)
            if part
        )

        return _db_result(
            "db_profile",
            ok=True,
            duration_ms=total_duration,
            profile_name=runtime.profile.name,
            stdout_text=stdout_text,
            stderr_text=stderr_text,
            data={
                "schema": schema_name,
                "table": relation_name,
                "relation": relation_rows[0] if relation_rows else {},
                "columns": columns,
                "temporal_bounds": temporal_bounds,
                "profile": runtime.profile.redacted(),
            },
        )

    @mcp.tool(structured_output=True)
    async def db_export_csv(query: str, profile: str = "") -> ToolResult:
        """Export a query result directly to a CSV artifact."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_export_csv", started=started, profile_name=profile)

        export_query = f"COPY ({_normalize_query(query)}) TO STDOUT WITH CSV HEADER"
        result, duration_ms = await _run_sql(export_query, runtime=runtime, timeout=60, csv_output=False)
        error_code, error_stage, message = _db_error(result)
        extra_artifacts = None
        if result.stdout:
            extra_artifacts = [
                get_artifacts().write_text(
                    tool_name="db_export_csv",
                    channel="export",
                    content=result.stdout,
                    request_id=tool_context("db_export_csv").request_id,
                    suffix=".csv",
                )
            ]
        return _db_result(
            "db_export_csv",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            extra_artifacts=extra_artifacts,
            data={
                "query": query,
                "profile": runtime.profile.redacted(),
                "csv_exported": bool(result.stdout),
            },
        )

    async def _explain_query(query: str, analyze: bool, profile: str, tool_name: str) -> ToolResult:
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error(tool_name, started=started, profile_name=profile)

        prefix = "EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)" if analyze else "EXPLAIN (FORMAT TEXT)"
        result, duration_ms = await _run_sql(
            f"{prefix} {_normalize_query(query)}",
            runtime=runtime,
            timeout=60,
            csv_output=False,
        )
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            tool_name,
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={"query": query, "analyze": analyze, "profile": runtime.profile.redacted()},
        )

    @mcp.tool(structured_output=True)
    async def db_explain(query: str, analyze: bool = False, profile: str = "") -> ToolResult:
        """Run EXPLAIN or EXPLAIN ANALYZE for a query."""
        return await _explain_query(query, analyze, profile, "db_explain")

    @mcp.tool(structured_output=True)
    async def db_query_explain(query: str, analyze: bool = False, profile: str = "") -> ToolResult:
        """Alias of db_explain with a more explicit name for agent workflows."""
        return await _explain_query(query, analyze, profile, "db_query_explain")

    @mcp.tool(structured_output=True)
    async def db_execute(statement: str, profile: str = "") -> ToolResult:
        """Execute a SQL statement without enforcing read-only mode."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_execute", started=started, profile_name=profile)

        result, duration_ms = await _run_sql(_normalize_query(statement), runtime=runtime, timeout=60, csv_output=False)
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_execute",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={"statement": statement, "profile": runtime.profile.redacted()},
        )

    @mcp.tool(structured_output=True)
    async def db_size(profile: str = "") -> ToolResult:
        """Show database size and connection counts."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_size", started=started, profile_name=profile)

        query = (
            "SELECT current_database() AS database_name, "
            "pg_size_pretty(pg_database_size(current_database())) AS database_size, "
            "(SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()) AS active_connections"
        )
        result, rows, duration_ms = await _query_rows(query=query, runtime=runtime, timeout=15)
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_size",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={"info": rows[0] if rows else {}, "profile": runtime.profile.redacted()},
        )

    @mcp.tool(structured_output=True)
    async def db_indexes(table_name: str = "", profile: str = "") -> ToolResult:
        """List indexes, optionally scoped to a table."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_indexes", started=started, profile_name=profile)

        where = ""
        if table_name:
            try:
                schema_name, relation_name = _parse_relation_name(table_name)
            except ValueError as exc:
                return _db_result(
                    "db_indexes",
                    ok=False,
                    duration_ms=(time.monotonic() - started) * 1000,
                    profile_name=runtime.profile.name,
                    error_code="INVALID_RELATION_NAME",
                    error_stage="validation",
                    message=str(exc),
                )
            where = (
                f"WHERE schemaname = {_sql_literal(schema_name)} "
                f"AND tablename = {_sql_literal(relation_name)} "
            )
        query = (
            "SELECT schemaname, tablename, indexname, indexdef "
            f"FROM pg_indexes {where} ORDER BY schemaname, tablename, indexname"
        )
        result, rows, duration_ms = await _query_rows(query=query, runtime=runtime, timeout=30)
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_indexes",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={"indexes": rows, "table": table_name or None},
        )

    @mcp.tool(structured_output=True)
    async def db_connections(profile: str = "") -> ToolResult:
        """Inspect active PostgreSQL connections."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_connections", started=started, profile_name=profile)

        query = (
            "SELECT usename, application_name, client_addr, state, wait_event_type, query_start "
            "FROM pg_stat_activity WHERE datname = current_database() ORDER BY query_start DESC LIMIT 100"
        )
        result, rows, duration_ms = await _query_rows(query=query, runtime=runtime, timeout=30)
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_connections",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={"connections": rows},
        )

    @mcp.tool(structured_output=True)
    async def db_table_stats(table_name: str = "", profile: str = "") -> ToolResult:
        """Inspect tuple and scan stats for database tables."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_table_stats", started=started, profile_name=profile)

        where = ""
        if table_name:
            try:
                schema_name, relation_name = _parse_relation_name(table_name)
            except ValueError as exc:
                return _db_result(
                    "db_table_stats",
                    ok=False,
                    duration_ms=(time.monotonic() - started) * 1000,
                    profile_name=runtime.profile.name,
                    error_code="INVALID_RELATION_NAME",
                    error_stage="validation",
                    message=str(exc),
                )
            where = (
                f"WHERE schemaname = {_sql_literal(schema_name)} "
                f"AND relname = {_sql_literal(relation_name)} "
            )
        query = (
            "SELECT schemaname, relname, seq_scan, idx_scan, n_live_tup, n_dead_tup, last_vacuum, last_autovacuum "
            f"FROM pg_stat_user_tables {where} ORDER BY n_live_tup DESC NULLS LAST"
        )
        result, rows, duration_ms = await _query_rows(query=query, runtime=runtime, timeout=30)
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_table_stats",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={"stats": rows, "table": table_name or None},
        )

    @mcp.tool(structured_output=True)
    async def db_extensions(profile: str = "") -> ToolResult:
        """List installed PostgreSQL extensions."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_extensions", started=started, profile_name=profile)

        result, rows, duration_ms = await _query_rows(
            query="SELECT extname, extversion FROM pg_extension ORDER BY extname",
            runtime=runtime,
            timeout=20,
        )
        error_code, error_stage, message = _db_error(result)
        return _db_result(
            "db_extensions",
            ok=result.ok,
            duration_ms=duration_ms,
            profile_name=runtime.profile.name,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=message,
            exit_code=result.exit_code,
            data={"extensions": rows},
        )

    @mcp.tool(structured_output=True)
    async def db_join_suggest(table_a: str, table_b: str, profile: str = "") -> ToolResult:
        """Suggest join candidates from PostgreSQL constraints and exact compatible column matches."""
        started = time.monotonic()
        runtime = await _database_runtime(profile)
        if runtime is None:
            return _config_error("db_join_suggest", started=started, profile_name=profile)

        try:
            schema_a, relation_a = _parse_relation_name(table_a)
            schema_b, relation_b = _parse_relation_name(table_b)
        except ValueError as exc:
            return _db_result(
                "db_join_suggest",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                profile_name=runtime.profile.name,
                error_code="INVALID_RELATION_NAME",
                error_stage="validation",
                message=str(exc),
            )

        fk_query = (
            "SELECT conname, pg_get_constraintdef(c.oid, true) AS definition "
            "FROM pg_constraint c "
            "JOIN pg_class rel ON rel.oid = c.conrelid "
            "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
            "JOIN pg_class ref_rel ON ref_rel.oid = c.confrelid "
            "JOIN pg_namespace ref_ns ON ref_ns.oid = ref_rel.relnamespace "
            "WHERE c.contype = 'f' AND ("
            f"(ns.nspname = {_sql_literal(schema_a)} AND rel.relname = {_sql_literal(relation_a)} "
            f"AND ref_ns.nspname = {_sql_literal(schema_b)} AND ref_rel.relname = {_sql_literal(relation_b)}) OR "
            f"(ns.nspname = {_sql_literal(schema_b)} AND rel.relname = {_sql_literal(relation_b)} "
            f"AND ref_ns.nspname = {_sql_literal(schema_a)} AND ref_rel.relname = {_sql_literal(relation_a)})"
            ")"
        )
        column_query = (
            "WITH cols_a AS ("
            "  SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type, "
            "         EXISTS ("
            "           SELECT 1 FROM pg_index i "
            "           WHERE i.indrelid = c.oid AND i.indisprimary AND a.attnum = ANY(i.indkey)"
            "         ) AS is_primary, "
            "         EXISTS ("
            "           SELECT 1 FROM pg_index i "
            "           WHERE i.indrelid = c.oid AND i.indisunique AND a.attnum = ANY(i.indkey)"
            "         ) AS is_unique "
            "  FROM pg_attribute a "
            "  JOIN pg_class c ON c.oid = a.attrelid "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"  WHERE n.nspname = {_sql_literal(schema_a)} AND c.relname = {_sql_literal(relation_a)} "
            "    AND a.attnum > 0 AND NOT a.attisdropped"
            "), cols_b AS ("
            "  SELECT a.attname AS column_name, format_type(a.atttypid, a.atttypmod) AS data_type, "
            "         EXISTS ("
            "           SELECT 1 FROM pg_index i "
            "           WHERE i.indrelid = c.oid AND i.indisprimary AND a.attnum = ANY(i.indkey)"
            "         ) AS is_primary, "
            "         EXISTS ("
            "           SELECT 1 FROM pg_index i "
            "           WHERE i.indrelid = c.oid AND i.indisunique AND a.attnum = ANY(i.indkey)"
            "         ) AS is_unique "
            "  FROM pg_attribute a "
            "  JOIN pg_class c ON c.oid = a.attrelid "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"  WHERE n.nspname = {_sql_literal(schema_b)} AND c.relname = {_sql_literal(relation_b)} "
            "    AND a.attnum > 0 AND NOT a.attisdropped"
            ") "
            "SELECT cols_a.column_name AS column_a, cols_b.column_name AS column_b, cols_a.data_type, "
            "cols_a.is_primary AS a_is_primary, cols_b.is_primary AS b_is_primary, "
            "cols_a.is_unique AS a_is_unique, cols_b.is_unique AS b_is_unique "
            "FROM cols_a JOIN cols_b "
            "ON cols_a.column_name = cols_b.column_name AND cols_a.data_type = cols_b.data_type "
            "ORDER BY cols_a.is_primary DESC, cols_b.is_primary DESC, "
            "cols_a.is_unique DESC, cols_b.is_unique DESC, cols_a.column_name"
        )

        fk_result, fk_rows, fk_ms = await _query_rows(query=fk_query, runtime=runtime, timeout=20)
        cols_result, cols_rows, cols_ms = await _query_rows(query=column_query, runtime=runtime, timeout=20)
        if not fk_result.ok:
            error_code, error_stage, message = _db_error(fk_result)
            return _db_result(
                "db_join_suggest",
                ok=False,
                duration_ms=fk_ms,
                profile_name=runtime.profile.name,
                stdout_text=fk_result.stdout,
                stderr_text=fk_result.stderr,
                error_code=error_code,
                error_stage=error_stage,
                message=message,
                exit_code=fk_result.exit_code,
            )
        if not cols_result.ok:
            error_code, error_stage, message = _db_error(cols_result)
            return _db_result(
                "db_join_suggest",
                ok=False,
                duration_ms=cols_ms,
                profile_name=runtime.profile.name,
                stdout_text=cols_result.stdout,
                stderr_text=cols_result.stderr,
                error_code=error_code,
                error_stage=error_stage,
                message=message,
                exit_code=cols_result.exit_code,
            )

        suggestions = [
            {
                "column_a": row["column_a"],
                "column_b": row["column_b"],
                "data_type": row["data_type"],
                "score": 0.9 if row["a_is_primary"] == "t" or row["b_is_primary"] == "t" else 0.7,
                "rationale": "exact column name and data type match",
                "a_is_primary": row["a_is_primary"] == "t",
                "b_is_primary": row["b_is_primary"] == "t",
                "a_is_unique": row["a_is_unique"] == "t",
                "b_is_unique": row["b_is_unique"] == "t",
            }
            for row in cols_rows
        ]
        for row in fk_rows:
            suggestions.insert(
                0,
                {
                    "constraint": row["conname"],
                    "definition": row["definition"],
                    "score": 1.0,
                    "rationale": "foreign key constraint exists in catalog",
                },
            )

        return _db_result(
            "db_join_suggest",
            ok=True,
            duration_ms=fk_ms + cols_ms,
            profile_name=runtime.profile.name,
            stdout_text="\n".join(part for part in (fk_result.stdout, cols_result.stdout) if part),
            stderr_text="\n".join(part for part in (fk_result.stderr, cols_result.stderr) if part),
            data={
                "table_a": {"schema": schema_a, "table": relation_a},
                "table_b": {"schema": schema_b, "table": relation_b},
                "suggestions": suggestions,
            },
        )
