"""Terminal / command execution tools."""

from __future__ import annotations

import json
import shlex

from mcp.server.fastmcp import FastMCP

from mcp_nexus.server import get_pool


def register(mcp: FastMCP):

    @mcp.tool()
    async def execute_command(command: str, cwd: str = "", timeout: int = 60) -> str:
        """Execute a shell command on the remote server.

        Args:
            command: The shell command to execute.
            cwd: Working directory (optional).
            timeout: Maximum execution time in seconds (default 60, max 600).
        """
        timeout = min(timeout, 600)
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full(command, timeout=timeout, cwd=cwd)
            return json.dumps({
                "exit_code": result.exit_code,
                "stdout": result.stdout[-50000:] if len(result.stdout) > 50000 else result.stdout,
                "stderr": result.stderr[-10000:] if len(result.stderr) > 10000 else result.stderr,
                "truncated": len(result.stdout) > 50000,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def execute_script(script: str, interpreter: str = "bash", cwd: str = "", timeout: int = 120) -> str:
        """Execute a multi-line script on the remote server.

        Args:
            script: The script content (multi-line supported).
            interpreter: Script interpreter (bash, python3, sh, etc.).
            cwd: Working directory (optional).
            timeout: Maximum execution time in seconds.
        """
        timeout = min(timeout, 600)
        pool = get_pool()
        conn = await pool.acquire()
        try:
            # Write script to temp file, execute, clean up
            tmp = "/tmp/_nexus_script_$$"
            setup = f"cat > {tmp} << 'NEXUS_SCRIPT_EOF'\n{script}\nNEXUS_SCRIPT_EOF"
            if cwd:
                run_cmd = f"{setup} && cd {shlex.quote(cwd)} && {interpreter} {tmp}; _rc=$?; rm -f {tmp}; exit $_rc"
            else:
                run_cmd = f"{setup} && {interpreter} {tmp}; _rc=$?; rm -f {tmp}; exit $_rc"
            result = await conn.run_full(run_cmd, timeout=timeout)
            return json.dumps({
                "exit_code": result.exit_code,
                "stdout": result.stdout[-50000:],
                "stderr": result.stderr[-10000:],
                "interpreter": interpreter,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def environment_info() -> str:
        """Get server environment information (OS, kernel, shell, Python, etc.)."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = (
                "echo '---OS---' && cat /etc/os-release 2>/dev/null | head -5 && "
                "echo '---KERNEL---' && uname -a && "
                "echo '---HOSTNAME---' && hostname && "
                "echo '---UPTIME---' && uptime && "
                "echo '---PYTHON---' && python3 --version 2>/dev/null && "
                "echo '---NODE---' && node --version 2>/dev/null && "
                "echo '---SHELL---' && echo $SHELL && "
                "echo '---WHOAMI---' && whoami"
            )
            result = await conn.run_full(cmd, timeout=15)
            return json.dumps({"info": result.stdout.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def which_command(name: str) -> str:
        """Check if a command exists and show its path.

        Args:
            name: Command name to look up.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"which {shlex.quote(name)} 2>/dev/null && {shlex.quote(name)} --version 2>/dev/null | head -1"
            result = await conn.run_full(cmd)
            return json.dumps({
                "command": name,
                "found": result.ok,
                "output": result.stdout.strip() if result.ok else None,
            })
        finally:
            pool.release(conn)
