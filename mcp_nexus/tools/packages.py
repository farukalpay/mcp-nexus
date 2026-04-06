"""Package management tools — pip, apt, npm."""

from __future__ import annotations

import json
import shlex

from mcp.server.fastmcp import FastMCP

from mcp_nexus.server import get_pool


def register(mcp: FastMCP):

    @mcp.tool()
    async def pip_list(venv: str = "", pattern: str = "") -> str:
        """List installed Python packages.

        Args:
            venv: Path to virtualenv (optional — uses system Python if empty).
            pattern: Filter by package name substring.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            pip = f"{venv}/bin/pip" if venv else "pip3"
            cmd = f"{pip} list --format=columns 2>&1"
            if pattern:
                cmd += f" | grep -i {shlex.quote(pattern)}"
            result = await conn.run_full(cmd, timeout=30)
            return json.dumps({"packages": result.stdout.strip(), "venv": venv or "(system)"})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def pip_show(package: str, venv: str = "") -> str:
        """Show details for a Python package (version, location, dependencies).

        Args:
            package: Package name.
            venv: Path to virtualenv (optional).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            pip = f"{venv}/bin/pip" if venv else "pip3"
            result = await conn.run_full(f"{pip} show {shlex.quote(package)} 2>&1", timeout=15)
            return json.dumps({
                "package": package,
                "found": result.exit_code == 0,
                "info": result.stdout.strip() if result.ok else result.stderr.strip(),
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def apt_list(pattern: str = "", upgradable: bool = False) -> str:
        """List installed system packages (Debian/Ubuntu).

        Args:
            pattern: Filter by package name.
            upgradable: Show only upgradable packages.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if upgradable:
                cmd = "apt list --upgradable 2>/dev/null | tail -n +2"
            elif pattern:
                cmd = f"dpkg -l | grep -i {shlex.quote(pattern)} | head -50"
            else:
                cmd = "dpkg -l | tail -n +6 | head -100"
            result = await conn.run_full(cmd, timeout=30)
            return json.dumps({"packages": result.stdout.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def apt_install(packages: str, dry_run: bool = True) -> str:
        """Install system packages via apt (Debian/Ubuntu).

        Args:
            packages: Space-separated package names.
            dry_run: If True, simulate install only (default: True for safety).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            flag = "--dry-run" if dry_run else "-y"
            cmd = f"DEBIAN_FRONTEND=noninteractive apt-get install {flag} {packages} 2>&1"
            result = await conn.run_full(cmd, timeout=120)
            return json.dumps({
                "packages": packages,
                "dry_run": dry_run,
                "output": result.stdout[-10000:],
                "exit_code": result.exit_code,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def npm_list(path: str = "", global_packages: bool = False) -> str:
        """List installed npm packages.

        Args:
            path: Project directory (for local packages).
            global_packages: List global packages instead of local.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            check = await conn.run_full("which npm 2>/dev/null")
            if not check.ok:
                return json.dumps({"error": "npm not installed"})

            if global_packages:
                cmd = "npm list -g --depth=0 2>&1"
            elif path:
                cmd = f"cd {shlex.quote(path)} && npm list --depth=0 2>&1"
            else:
                cmd = "npm list --depth=0 2>&1"
            result = await conn.run_full(cmd, timeout=30)
            return json.dumps({"packages": result.stdout.strip(), "scope": "global" if global_packages else path or "."})
        finally:
            pool.release(conn)
