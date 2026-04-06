"""Process and systemd service management tools."""

from __future__ import annotations

import json
import shlex

from mcp.server.fastmcp import FastMCP

from mcp_nexus.server import get_pool


def register(mcp: FastMCP):

    @mcp.tool()
    async def list_services(filter_pattern: str = "") -> str:
        """List systemd services, optionally filtered.

        Args:
            filter_pattern: Filter services by name pattern (optional).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if filter_pattern:
                cmd = f"systemctl list-units --type=service --all | grep -i {shlex.quote(filter_pattern)}"
            else:
                cmd = "systemctl list-units --type=service --state=running --no-pager"
            result = await conn.run_full(cmd, timeout=15)
            return json.dumps({"services": result.stdout.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def service_status(service_name: str) -> str:
        """Get detailed status of a systemd service.

        Args:
            service_name: Service name (e.g., "lightcap", "nginx").
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full(f"systemctl status {shlex.quote(service_name)} --no-pager -l", timeout=10)
            is_active = await conn.run_full(f"systemctl is-active {shlex.quote(service_name)}")
            is_enabled = await conn.run_full(f"systemctl is-enabled {shlex.quote(service_name)}")
            return json.dumps({
                "service": service_name,
                "active": is_active.stdout.strip(),
                "enabled": is_enabled.stdout.strip(),
                "details": result.stdout.strip(),
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def restart_service(service_name: str) -> str:
        """Restart a systemd service.

        Args:
            service_name: Service name to restart.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full(f"systemctl restart {shlex.quote(service_name)}", timeout=30)
            status = await conn.run_full(f"systemctl is-active {shlex.quote(service_name)}")
            return json.dumps({
                "service": service_name,
                "restarted": result.ok,
                "status": status.stdout.strip(),
                "error": result.stderr.strip() if not result.ok else None,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def start_service(service_name: str) -> str:
        """Start a systemd service.

        Args:
            service_name: Service name to start.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full(f"systemctl start {shlex.quote(service_name)}", timeout=30)
            status = await conn.run_full(f"systemctl is-active {shlex.quote(service_name)}")
            return json.dumps({
                "service": service_name,
                "started": result.ok,
                "status": status.stdout.strip(),
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def stop_service(service_name: str) -> str:
        """Stop a systemd service.

        Args:
            service_name: Service name to stop.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full(f"systemctl stop {shlex.quote(service_name)}", timeout=30)
            return json.dumps({
                "service": service_name,
                "stopped": result.ok,
                "error": result.stderr.strip() if not result.ok else None,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def view_logs(service_name: str, lines: int = 100, since: str = "", follow: bool = False) -> str:
        """View systemd journal logs for a service.

        Args:
            service_name: Service name.
            lines: Number of recent lines to show.
            since: Time filter (e.g., "1h", "30min", "2024-01-01").
            follow: (ignored in MCP context, included for API compat).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"journalctl -u {shlex.quote(service_name)} --no-pager -n {lines}"
            if since:
                cmd += f" --since {shlex.quote(since)}"
            result = await conn.run(cmd, timeout=15)
            return json.dumps({"service": service_name, "logs": result.strip()[-30000:]})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def list_processes(filter_pattern: str = "", sort_by: str = "cpu") -> str:
        """List running processes.

        Args:
            filter_pattern: Filter by process name.
            sort_by: Sort by "cpu" or "mem".
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            sort_key = "-%cpu" if sort_by == "cpu" else "-%mem"
            cmd = f"ps aux --sort={sort_key} | head -30"
            if filter_pattern:
                cmd = f"ps aux | grep -i {shlex.quote(filter_pattern)} | grep -v grep"
            result = await conn.run(cmd, timeout=10)
            return json.dumps({"processes": result.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def kill_process(pid: int, signal: str = "TERM") -> str:
        """Send a signal to a process.

        Args:
            pid: Process ID.
            signal: Signal name (TERM, KILL, HUP, etc.).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full(f"kill -{shlex.quote(signal)} {pid}")
            return json.dumps({
                "pid": pid, "signal": signal, "ok": result.ok,
                "error": result.stderr.strip() if not result.ok else None,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def cron_list() -> str:
        """List all crontab entries."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full("crontab -l 2>/dev/null")
            return json.dumps({"crontab": result.stdout.strip() if result.ok else "(no crontab)"})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def cron_add(schedule: str, command: str) -> str:
        """Add a crontab entry.

        Args:
            schedule: Cron schedule (e.g., "0 */6 * * *").
            command: Command to execute.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            entry = f"{schedule} {command}"
            cmd = f'(crontab -l 2>/dev/null; echo {shlex.quote(entry)}) | sort -u | crontab -'
            result = await conn.run_full(cmd)
            return json.dumps({"added": entry, "ok": result.ok})
        finally:
            pool.release(conn)
