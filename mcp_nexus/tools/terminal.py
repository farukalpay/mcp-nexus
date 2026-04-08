"""Terminal and runtime inspection tools."""

from __future__ import annotations

import shlex
import time
from pathlib import PurePosixPath
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_nexus.results import ToolResult, build_tool_result
from mcp_nexus.runtime import ExecutionLimits, ExecutionRequest, build_managed_command, extract_execution_metadata
from mcp_nexus.server import get_artifacts, get_pool, get_settings, tool_context
from mcp_nexus.transport.ssh import CommandResult


def _safe_cwd(cwd: str) -> str:
    settings = get_settings()
    return cwd or settings.default_cwd


def _sandbox_root() -> str:
    settings = get_settings()
    return settings.sandbox_root


def _sandbox_path(name: str = "", path: str = "") -> str:
    if path:
        return path
    suffix = name or f"python-{int(time.time())}"
    return str(PurePosixPath(_sandbox_root()) / suffix)


def _error_code_for_result(result: CommandResult) -> tuple[str | None, str | None]:
    if result.ok:
        return None, None
    stderr = result.stderr.lower()
    if result.exit_code == 124 or "timed out" in stderr:
        return "TIMEOUT", "execution"
    return "COMMAND_FAILED", "execution"


async def _run_execution(
    *,
    command: str,
    cwd: str = "",
    timeout: int = 60,
    env: dict[str, str] | None = None,
    capture_usage: bool = True,
    cpu_limit_sec: int = 0,
    memory_limit_mb: int = 0,
    file_size_limit_mb: int = 0,
    process_limit: int = 0,
) -> tuple[CommandResult, dict[str, Any] | None, dict[str, Any], float]:
    settings = get_settings()
    timeout = min(timeout or settings.default_command_timeout, 600)
    pool = get_pool()
    conn = await pool.acquire()
    try:
        capabilities = await conn.probe_capabilities()
        request = ExecutionRequest(
            command=command,
            cwd=_safe_cwd(cwd),
            timeout=timeout,
            env=env or {},
            capture_usage=capture_usage,
            limits=ExecutionLimits(
                cpu_seconds=max(0, cpu_limit_sec),
                memory_mb=max(0, memory_limit_mb),
                file_size_mb=max(0, file_size_limit_mb),
                process_count=max(0, process_limit),
            ),
        )
        start = time.monotonic()
        result = await conn.run_full(build_managed_command(capabilities, request), timeout=timeout)
        duration_ms = (time.monotonic() - start) * 1000
        stderr, usage = extract_execution_metadata(result.stderr)
        normalized = CommandResult(stdout=result.stdout, stderr=stderr, exit_code=result.exit_code)
        capability_data = {
            "system": capabilities.system,
            "python_command": capabilities.python_command,
            "package_manager": capabilities.package_manager,
            "service_manager": capabilities.service_manager,
        }
        return normalized, usage, capability_data, duration_ms
    finally:
        pool.release(conn)


def _result(
    tool_name: str,
    *,
    ok: bool,
    duration_ms: float,
    stdout_text: str = "",
    stderr_text: str = "",
    error_code: str | None = None,
    error_stage: str | None = None,
    message: str | None = None,
    exit_code: int | None = None,
    data: Any = None,
    usage: dict[str, Any] | None = None,
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
        data=data,
        exit_code=exit_code,
        resource_usage=usage,
    )


async def _ensure_python_sandbox(
    *,
    sandbox_path: str,
    requirements: list[str] | None = None,
    recreate: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pool = get_pool()
    conn = await pool.acquire()
    try:
        capabilities = await conn.probe_capabilities()
        if not capabilities.python_command:
            raise RuntimeError("Python is not available on the target host")

        sandbox = _sandbox_path(path=sandbox_path)
        base_dir = str(PurePosixPath(sandbox).parent)
        if recreate:
            await conn.run_full(f"rm -rf {shlex.quote(sandbox)}")

        exists = await conn.file_exists(f"{sandbox}/pyvenv.cfg")
        if not exists:
            cmd = (
                f"mkdir -p {shlex.quote(base_dir)} && "
                f"{shlex.quote(capabilities.python_command)} -m venv {shlex.quote(sandbox)} && "
                f"{shlex.quote(sandbox)}/bin/python -m pip install --upgrade pip"
            )
            result = await conn.run_full(cmd, timeout=180)
            if not result.ok:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "sandbox creation failed")

        if requirements:
            packages = " ".join(shlex.quote(req) for req in requirements)
            install = await conn.run_full(f"{shlex.quote(sandbox)}/bin/pip install {packages}", timeout=240)
            if not install.ok:
                raise RuntimeError(install.stderr.strip() or install.stdout.strip() or "sandbox install failed")

        return (
            {
                "path": sandbox,
                "python": f"{sandbox}/bin/python",
                "pip": f"{sandbox}/bin/pip",
                "requirements": requirements or [],
            },
            capabilities.to_dict(),
        )
    finally:
        pool.release(conn)


def register(mcp: FastMCP):

    @mcp.tool(structured_output=True)
    async def execute_command(
        command: str,
        cwd: str = "",
        timeout: int = 60,
        env: dict[str, str] | None = None,
        capture_usage: bool = True,
        cpu_limit_sec: int = 0,
        memory_limit_mb: int = 0,
        file_size_limit_mb: int = 0,
        process_limit: int = 0,
    ) -> ToolResult:
        """Execute a shell command with structured stdout/stderr and artifact fallbacks."""
        started = time.monotonic()
        try:
            result, usage, capabilities, duration_ms = await _run_execution(
                command=command,
                cwd=cwd,
                timeout=timeout,
                env=env,
                capture_usage=capture_usage,
                cpu_limit_sec=cpu_limit_sec,
                memory_limit_mb=memory_limit_mb,
                file_size_limit_mb=file_size_limit_mb,
                process_limit=process_limit,
            )
        except Exception as exc:
            return _result(
                "execute_command",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                stderr_text=str(exc),
                error_code="EXECUTION_SETUP_FAILED",
                error_stage="setup",
                message="Failed to prepare command execution.",
            )

        error_code, error_stage = _error_code_for_result(result)
        return _result(
            "execute_command",
            ok=result.ok,
            duration_ms=duration_ms,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=None if result.ok else "Command execution failed.",
            exit_code=result.exit_code,
            data={"capabilities": capabilities, "cwd": _safe_cwd(cwd) or None},
            usage=usage,
        )

    @mcp.tool(structured_output=True)
    async def execute_script(
        script: str,
        interpreter: str = "bash",
        cwd: str = "",
        timeout: int = 120,
        env: dict[str, str] | None = None,
        capture_usage: bool = True,
        cpu_limit_sec: int = 0,
        memory_limit_mb: int = 0,
    ) -> ToolResult:
        """Execute a multi-line script through the chosen interpreter."""
        temp_path = "/tmp/_nexus_script_$$"
        command = (
            f"cat > {temp_path} << 'NEXUS_SCRIPT_EOF'\n{script}\nNEXUS_SCRIPT_EOF\n"
            f"{shlex.quote(interpreter)} {temp_path}; _rc=$?; rm -f {temp_path}; exit $_rc"
        )
        started = time.monotonic()
        try:
            result, usage, capabilities, duration_ms = await _run_execution(
                command=command,
                cwd=cwd,
                timeout=timeout,
                env=env,
                capture_usage=capture_usage,
                cpu_limit_sec=cpu_limit_sec,
                memory_limit_mb=memory_limit_mb,
            )
        except Exception as exc:
            return _result(
                "execute_script",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                stderr_text=str(exc),
                error_code="EXECUTION_SETUP_FAILED",
                error_stage="setup",
                message="Failed to prepare script execution.",
                data={"interpreter": interpreter},
            )

        error_code, error_stage = _error_code_for_result(result)
        return _result(
            "execute_script",
            ok=result.ok,
            duration_ms=duration_ms,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=None if result.ok else "Script execution failed.",
            exit_code=result.exit_code,
            data={"interpreter": interpreter, "capabilities": capabilities, "cwd": _safe_cwd(cwd) or None},
            usage=usage,
        )

    @mcp.tool(structured_output=True)
    async def execute_python(
        code: str,
        cwd: str = "",
        timeout: int = 120,
        sandbox_path: str = "",
        requirements: list[str] | None = None,
        env: dict[str, str] | None = None,
        capture_usage: bool = True,
        cpu_limit_sec: int = 60,
        memory_limit_mb: int = 512,
    ) -> ToolResult:
        """Execute Python code directly or inside a reusable virtualenv sandbox."""
        runtime_env = dict(env or {})
        sandbox_info: dict[str, Any] | None = None
        python_bin = "python3"
        started = time.monotonic()

        pool = get_pool()
        conn = await pool.acquire()
        try:
            capabilities = await conn.probe_capabilities()
        finally:
            pool.release(conn)

        if not capabilities.python_command:
            return _result(
                "execute_python",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                error_code="PYTHON_UNAVAILABLE",
                error_stage="capability_probe",
                message="Python is not available on the target host.",
                data={"capabilities": capabilities.to_dict()},
            )
        python_bin = capabilities.python_command

        try:
            if sandbox_path or requirements:
                sandbox_info, _ = await _ensure_python_sandbox(
                    sandbox_path=sandbox_path or _sandbox_path(),
                    requirements=requirements,
                )
                python_bin = str(sandbox_info["python"])
                sandbox_root = str(sandbox_info["path"])
                runtime_env["VIRTUAL_ENV"] = sandbox_root
                runtime_env["PATH"] = f"{sandbox_root}/bin:$PATH"

            temp_path = "/tmp/_nexus_python_$$.py"
            command = (
                f"cat > {temp_path} << 'NEXUS_PYTHON_EOF'\n{code}\nNEXUS_PYTHON_EOF\n"
                f"{shlex.quote(python_bin)} {temp_path}; _rc=$?; rm -f {temp_path}; exit $_rc"
            )
            result, usage, capability_data, duration_ms = await _run_execution(
                command=command,
                cwd=cwd,
                timeout=timeout,
                env=runtime_env,
                capture_usage=capture_usage,
                cpu_limit_sec=cpu_limit_sec,
                memory_limit_mb=memory_limit_mb,
            )
        except Exception as exc:
            return _result(
                "execute_python",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                stderr_text=str(exc),
                error_code="PYTHON_EXECUTION_SETUP_FAILED",
                error_stage="setup",
                message="Failed to prepare Python execution.",
                data={"sandbox": sandbox_info},
            )

        error_code, error_stage = _error_code_for_result(result)
        return _result(
            "execute_python",
            ok=result.ok,
            duration_ms=duration_ms,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=None if result.ok else "Python execution failed.",
            exit_code=result.exit_code,
            data={
                "capabilities": capability_data,
                "sandbox": sandbox_info,
                "cwd": _safe_cwd(cwd) or None,
            },
            usage=usage,
        )

    @mcp.tool(structured_output=True)
    async def environment_info() -> ToolResult:
        """Get environment and runtime defaults for the connected target host."""
        settings = get_settings()
        started = time.monotonic()
        pool = get_pool()
        conn = await pool.acquire()
        try:
            capabilities = await conn.probe_capabilities()
            command = (
                "echo '---HOSTNAME---' && hostname && "
                "echo '---UPTIME---' && uptime && "
                "echo '---WHOAMI---' && whoami && "
                "echo '---SHELL---' && echo ${SHELL:-/bin/sh} && "
                "echo '---KERNEL---' && uname -a"
            )
            result = await conn.run_full(command, timeout=15)
        except Exception as exc:
            return _result(
                "environment_info",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                stderr_text=str(exc),
                error_code="ENVIRONMENT_INFO_FAILED",
                error_stage="inspection",
                message="Failed to inspect target environment.",
            )
        finally:
            pool.release(conn)

        error_code, error_stage = _error_code_for_result(result)
        return _result(
            "environment_info",
            ok=result.ok,
            duration_ms=(time.monotonic() - started) * 1000,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=None if result.ok else "Environment inspection failed.",
            exit_code=result.exit_code,
            data={
                "defaults": {
                    "cwd": settings.default_cwd or None,
                    "timeout_sec": settings.default_command_timeout,
                    "output_limit_bytes": settings.output_limit_bytes,
                    "sandbox_root": _sandbox_root(),
                },
                "capabilities": capabilities.to_dict(),
            },
        )

    @mcp.tool(structured_output=True)
    async def which_command(name: str) -> ToolResult:
        """Check if a command exists and show its resolved path."""
        started = time.monotonic()
        pool = get_pool()
        conn = await pool.acquire()
        try:
            command = (
                f"command -v {shlex.quote(name)} 2>/dev/null && {shlex.quote(name)} --version 2>/dev/null | head -1"
            )
            result = await conn.run_full(command)
        except Exception as exc:
            return _result(
                "which_command",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                stderr_text=str(exc),
                error_code="WHICH_FAILED",
                error_stage="inspection",
                message="Failed to resolve command path.",
                data={"command": name},
            )
        finally:
            pool.release(conn)

        error_code, error_stage = _error_code_for_result(result)
        return _result(
            "which_command",
            ok=result.ok,
            duration_ms=(time.monotonic() - started) * 1000,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=None if result.ok else "Command was not found.",
            exit_code=result.exit_code,
            data={"command": name, "found": result.ok},
        )

    @mcp.tool(structured_output=True)
    async def server_capabilities(refresh: bool = False) -> ToolResult:
        """Return the target host capabilities used for package, service, and sandbox defaults."""
        started = time.monotonic()
        pool = get_pool()
        conn = await pool.acquire()
        try:
            capabilities = await conn.probe_capabilities(refresh=refresh)
        except Exception as exc:
            return _result(
                "server_capabilities",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                stderr_text=str(exc),
                error_code="CAPABILITY_PROBE_FAILED",
                error_stage="inspection",
                message="Failed to probe server capabilities.",
            )
        finally:
            pool.release(conn)

        return _result(
            "server_capabilities",
            ok=True,
            duration_ms=(time.monotonic() - started) * 1000,
            data=capabilities.to_dict(),
        )

    @mcp.tool(structured_output=True)
    async def create_python_sandbox(
        name: str = "",
        path: str = "",
        requirements: list[str] | None = None,
        recreate: bool = False,
    ) -> ToolResult:
        """Create a reusable virtualenv sandbox on the target host."""
        started = time.monotonic()
        try:
            sandbox_info, capabilities = await _ensure_python_sandbox(
                sandbox_path=_sandbox_path(name=name, path=path),
                requirements=requirements,
                recreate=recreate,
            )
        except Exception as exc:
            return _result(
                "create_python_sandbox",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                stderr_text=str(exc),
                error_code="SANDBOX_CREATE_FAILED",
                error_stage="setup",
                message="Failed to create Python sandbox.",
            )

        return _result(
            "create_python_sandbox",
            ok=True,
            duration_ms=(time.monotonic() - started) * 1000,
            data={"sandbox": sandbox_info, "capabilities": capabilities},
        )

    @mcp.tool(structured_output=True)
    async def list_python_sandboxes(base_path: str = "") -> ToolResult:
        """List discovered Python virtualenv sandboxes under the configured sandbox root."""
        started = time.monotonic()
        pool = get_pool()
        conn = await pool.acquire()
        try:
            search_root = base_path or _sandbox_root()
            command = (
                f"test -d {shlex.quote(search_root)} || exit 0; "
                f"find {shlex.quote(search_root)} -maxdepth 3 -name pyvenv.cfg -print"
            )
            result = await conn.run_full(command, timeout=20)
        except Exception as exc:
            return _result(
                "list_python_sandboxes",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                stderr_text=str(exc),
                error_code="SANDBOX_LIST_FAILED",
                error_stage="inspection",
                message="Failed to list Python sandboxes.",
                data={"base_path": base_path or _sandbox_root()},
            )
        finally:
            pool.release(conn)

        sandboxes = []
        for cfg in filter(None, result.stdout.splitlines()):
            sandbox = str(PurePosixPath(cfg).parent)
            sandboxes.append({"path": sandbox, "python": f"{sandbox}/bin/python", "pip": f"{sandbox}/bin/pip"})

        error_code, error_stage = _error_code_for_result(result)
        return _result(
            "list_python_sandboxes",
            ok=result.ok,
            duration_ms=(time.monotonic() - started) * 1000,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=None if result.ok else "Failed to enumerate sandboxes.",
            exit_code=result.exit_code,
            data={"base_path": base_path or _sandbox_root(), "sandboxes": sandboxes},
        )

    @mcp.tool(structured_output=True)
    async def remove_python_sandbox(path: str) -> ToolResult:
        """Remove a Python sandbox created under the configured sandbox root."""
        started = time.monotonic()
        if not path:
            return _result(
                "remove_python_sandbox",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                error_code="PATH_REQUIRED",
                error_stage="validation",
                message="path is required",
            )

        root = PurePosixPath(_sandbox_root())
        target = PurePosixPath(path)
        if root not in target.parents and target != root:
            return _result(
                "remove_python_sandbox",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                error_code="SANDBOX_SCOPE_VIOLATION",
                error_stage="validation",
                message="sandbox removal is restricted to the configured sandbox root",
                data={"sandbox_root": str(root)},
            )

        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full(f"rm -rf {shlex.quote(path)}", timeout=60)
        except Exception as exc:
            return _result(
                "remove_python_sandbox",
                ok=False,
                duration_ms=(time.monotonic() - started) * 1000,
                stderr_text=str(exc),
                error_code="SANDBOX_REMOVE_FAILED",
                error_stage="execution",
                message="Failed to remove Python sandbox.",
                data={"path": path},
            )
        finally:
            pool.release(conn)

        error_code, error_stage = _error_code_for_result(result)
        return _result(
            "remove_python_sandbox",
            ok=result.ok,
            duration_ms=(time.monotonic() - started) * 1000,
            stdout_text=result.stdout,
            stderr_text=result.stderr,
            error_code=error_code,
            error_stage=error_stage,
            message=None if result.ok else "Sandbox removal failed.",
            exit_code=result.exit_code,
            data={"path": path, "removed": result.ok},
        )
