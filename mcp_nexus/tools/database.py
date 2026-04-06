"""Database operations — query, inspect, manage PostgreSQL."""

from __future__ import annotations

import json
import shlex

from mcp.server.fastmcp import FastMCP

from mcp_nexus.server import get_pool, get_settings


def _psql(query: str, settings) -> str:
    """Build a psql command string."""
    return (
        f"PGPASSWORD={shlex.quote(settings.db_password)} "
        f"psql -h {shlex.quote(settings.db_host)} "
        f"-p {settings.db_port} "
        f"-U {shlex.quote(settings.db_user)} "
        f"-d {shlex.quote(settings.db_name)} "
        f"-t -A -c {shlex.quote(query)}"
    )


def register(mcp: FastMCP):

    @mcp.tool()
    async def db_query(query: str, max_rows: int = 100) -> str:
        """Execute a SQL query (read-only recommended).

        Args:
            query: SQL query to execute.
            max_rows: Maximum rows to return.
        """
        settings = get_settings()
        if not settings.db_host:
            return json.dumps({"error": "No database configured. Set NEXUS_DB_* env vars."})

        pool = get_pool()
        conn = await pool.acquire()
        try:
            limited_query = query.rstrip(";")
            if "limit" not in limited_query.lower():
                limited_query += f" LIMIT {max_rows}"
            cmd = _psql(limited_query + ";", settings)
            result = await conn.run_full(cmd, timeout=30)
            if not result.ok:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({"query": query, "rows": result.stdout.strip(), "max_rows": max_rows})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def db_tables() -> str:
        """List all tables in the configured database."""
        settings = get_settings()
        if not settings.db_host:
            return json.dumps({"error": "No database configured."})

        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = _psql(
                "SELECT table_name, pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) as size "
                "FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;",
                settings,
            )
            result = await conn.run_full(cmd, timeout=15)
            if not result.ok:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({"tables": result.stdout.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def db_schema(table_name: str) -> str:
        """Get the schema (columns, types, constraints) of a table.

        Args:
            table_name: Table name to inspect.
        """
        settings = get_settings()
        if not settings.db_host:
            return json.dumps({"error": "No database configured."})

        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = _psql(
                f"SELECT column_name, data_type, is_nullable, column_default "
                f"FROM information_schema.columns "
                f"WHERE table_name={shlex.quote(table_name)} "
                f"ORDER BY ordinal_position;",
                settings,
            )
            result = await conn.run_full(cmd, timeout=15)
            if not result.ok:
                return json.dumps({"error": result.stderr.strip()})
            return json.dumps({"table": table_name, "columns": result.stdout.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def db_execute(statement: str) -> str:
        """Execute a SQL statement (INSERT, UPDATE, DELETE, CREATE, etc.).

        Args:
            statement: SQL statement to execute.
        """
        settings = get_settings()
        if not settings.db_host:
            return json.dumps({"error": "No database configured."})

        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = _psql(statement, settings)
            result = await conn.run_full(cmd, timeout=60)
            return json.dumps({
                "ok": result.ok,
                "output": result.stdout.strip(),
                "error": result.stderr.strip() if not result.ok else None,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def db_size() -> str:
        """Show database size and connection info."""
        settings = get_settings()
        if not settings.db_host:
            return json.dumps({"error": "No database configured."})

        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = _psql(
                f"SELECT pg_size_pretty(pg_database_size('{settings.db_name}')) as db_size, "
                f"(SELECT count(*) FROM pg_stat_activity WHERE datname='{settings.db_name}') as connections;",
                settings,
            )
            result = await conn.run_full(cmd, timeout=10)
            return json.dumps({
                "database": settings.db_name,
                "host": settings.db_host,
                "port": settings.db_port,
                "info": result.stdout.strip(),
            })
        finally:
            pool.release(conn)
