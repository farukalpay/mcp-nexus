"""Deployment tools — upload, sync, deploy pipeline, rollback."""

from __future__ import annotations

import json
import shlex

from mcp.server.fastmcp import FastMCP

from mcp_nexus.server import get_pool


def register(mcp: FastMCP):

    @mcp.tool()
    async def deploy_sync(
        local_path: str,
        remote_path: str,
        exclude: list[str] | None = None,
        dry_run: bool = False,
    ) -> str:
        """Sync files from a local path to the remote server using rsync.

        Args:
            local_path: Source directory on the server (or local if localhost mode).
            remote_path: Destination directory on the server.
            exclude: List of patterns to exclude (e.g., ["__pycache__", ".git"]).
            dry_run: If True, show what would be synced without actually doing it.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            excludes = exclude or ["__pycache__", "*.pyc", ".DS_Store", ".git", "node_modules"]
            exclude_flags = " ".join(f"--exclude={shlex.quote(e)}" for e in excludes)
            dry = "--dry-run" if dry_run else ""
            cmd = f"rsync -avz --delete {dry} {exclude_flags} {shlex.quote(local_path)}/ {shlex.quote(remote_path)}/"
            result = await conn.run_full(cmd, timeout=300)
            return json.dumps({
                "status": "ok" if result.ok else "error",
                "dry_run": dry_run,
                "output": result.stdout.strip()[-5000:],
                "errors": result.stderr.strip() if result.stderr else None,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def deploy_service(
        service_name: str,
        pre_command: str = "",
        post_command: str = "",
    ) -> str:
        """Deploy by restarting a service with optional pre/post commands.

        Args:
            service_name: Systemd service to restart.
            pre_command: Command to run before restart (e.g., pip install).
            post_command: Command to run after restart (e.g., health check).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            results = {"service": service_name}

            if pre_command:
                r = await conn.run_full(pre_command, timeout=120)
                results["pre_command"] = {"ok": r.ok, "output": r.stdout.strip()[-2000:]}
                if not r.ok:
                    results["error"] = "Pre-command failed, aborting deploy"
                    return json.dumps(results)

            r = await conn.run_full(f"systemctl restart {shlex.quote(service_name)}", timeout=30)
            results["restart"] = {"ok": r.ok}

            # Wait for service to stabilize
            import asyncio
            await asyncio.sleep(2)

            r = await conn.run_full(f"systemctl is-active {shlex.quote(service_name)}")
            results["status"] = r.stdout.strip()

            if post_command:
                r = await conn.run_full(post_command, timeout=60)
                results["post_command"] = {"ok": r.ok, "output": r.stdout.strip()[-2000:]}

            return json.dumps(results)
        finally:
            pool.release(conn)

    @mcp.tool()
    async def create_backup(path: str, backup_dir: str = "/root/backups") -> str:
        """Create a timestamped backup of a file or directory.

        Args:
            path: Path to back up.
            backup_dir: Directory to store backups.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            await conn.run_full(f"mkdir -p {shlex.quote(backup_dir)}")
            name = path.rstrip("/").rsplit("/", 1)[-1]
            timestamp = (await conn.run("date +%Y%m%d_%H%M%S")).strip()
            backup_path = f"{backup_dir}/{name}_{timestamp}.tar.gz"
            cmd = f"tar czf {shlex.quote(backup_path)} -C {shlex.quote(path.rsplit('/', 1)[0])} {shlex.quote(name)}"
            result = await conn.run_full(cmd, timeout=120)
            if result.ok:
                size = await conn.run(f"du -h {shlex.quote(backup_path)}")
                return json.dumps({"status": "ok", "backup": backup_path, "size": size.strip()})
            return json.dumps({"error": result.stderr.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def list_backups(backup_dir: str = "/root/backups") -> str:
        """List available backups.

        Args:
            backup_dir: Directory containing backups.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full(f"ls -lhtr {shlex.quote(backup_dir)}/*.tar.gz 2>/dev/null")
            return json.dumps({
                "backup_dir": backup_dir,
                "backups": result.stdout.strip() if result.ok else "(no backups found)",
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def restore_backup(backup_path: str, restore_to: str) -> str:
        """Restore from a backup archive.

        Args:
            backup_path: Path to the .tar.gz backup file.
            restore_to: Directory to restore into.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            exists = await conn.file_exists(backup_path)
            if not exists:
                return json.dumps({"error": f"Backup not found: {backup_path}"})

            cmd = f"tar xzf {shlex.quote(backup_path)} -C {shlex.quote(restore_to)}"
            result = await conn.run_full(cmd, timeout=120)
            return json.dumps({
                "status": "ok" if result.ok else "error",
                "backup": backup_path,
                "restored_to": restore_to,
                "error": result.stderr.strip() if not result.ok else None,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def pip_install(packages: str, venv_path: str = "") -> str:
        """Install Python packages (in a virtualenv if specified).

        Args:
            packages: Space-separated package names or -r requirements.txt.
            venv_path: Path to the Python virtualenv (optional — uses system pip if empty).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if venv_path:
                cmd = f"{shlex.quote(venv_path)}/bin/pip install {packages}"
            else:
                cmd = f"pip install {packages}"
            result = await conn.run_full(cmd, timeout=120)
            return json.dumps({
                "ok": result.ok,
                "output": result.stdout.strip()[-3000:],
                "errors": result.stderr.strip() if result.stderr else None,
            })
        finally:
            pool.release(conn)
