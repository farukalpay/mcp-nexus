"""MCP Nexus server — registers all tools and manages lifecycle."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_nexus.config import Settings
from mcp_nexus.gateway import GatewayManager
from mcp_nexus.intelligence.memory import MemoryEngine
from mcp_nexus.middleware.audit import AuditEntry, AuditLog
from mcp_nexus.middleware.rate_limit import RateLimiter
from mcp_nexus.transport.ssh import SSHPool

logger = logging.getLogger(__name__)

# Global state shared across tools
_gateway: GatewayManager | None = None
_settings: Settings | None = None
_memory: MemoryEngine | None = None
_audit: AuditLog | None = None
_rate_limiter: RateLimiter | None = None

# Per-request pool context (set by gateway middleware)
_current_pool: contextvars.ContextVar[SSHPool | None] = contextvars.ContextVar("_current_pool", default=None)


def get_pool() -> SSHPool:
    """Get the SSH pool for the current request.

    In gateway mode, returns the pool for the authenticated client.
    Falls back to the owner pool (localhost) when no auth is present.
    """
    # Check per-request context first
    pool = _current_pool.get()
    if pool is not None:
        return pool

    # Fall back to owner pool
    assert _gateway is not None, "Gateway not initialized — call create_server() first"
    return _gateway.get_owner_pool()


def set_current_pool(pool: SSHPool):
    """Set the SSH pool for the current request context."""
    _current_pool.set(pool)


def get_gateway() -> GatewayManager:
    assert _gateway is not None, "Gateway not initialized"
    return _gateway


def get_settings() -> Settings:
    assert _settings is not None, "Settings not initialized"
    return _settings


def get_memory() -> MemoryEngine | None:
    return _memory


def get_audit() -> AuditLog | None:
    return _audit


def create_server(settings: Settings | None = None) -> FastMCP:
    """Create and configure the MCP server with all tools."""
    global _gateway, _settings, _memory, _audit, _rate_limiter

    if settings is None:
        settings = Settings()
    _settings = settings
    _gateway = GatewayManager(settings)

    # Initialize middleware
    _audit = AuditLog(max_entries=5000)
    _rate_limiter = RateLimiter(rpm=settings.rate_limit_rpm, burst=settings.rate_limit_burst)

    # Initialize intelligence
    if settings.intelligence_enabled:
        _memory = MemoryEngine(data_dir=settings.data_dir)
        _memory.open()
        logger.info("Intelligence engine enabled — data at %s", settings.data_dir)

    mcp = FastMCP(
        "nexus",
        instructions=(
            "MCP Nexus provides full remote server management with built-in intelligence. "
            "Use these tools to read/write files, execute commands, manage services, "
            "query databases, monitor health, and deploy code on the connected server. "
            "The intelligence system learns from your usage — call nexus_recall to see "
            "what you were working on, or nexus_insights for usage analytics. "
            "All operations are audited and rate-limited. "
            "Authenticate with client_id=SERVER_IP and client_secret=SSH_PASSWORD to manage your server."
        ),
        host=settings.host,
        port=settings.port,
    )

    # ── Register all tool modules ──
    from mcp_nexus.tools import (
        database,
        debug,
        deploy,
        filesystem,
        git,
        intelligence,
        monitor,
        network,
        packages,
        process,
        terminal,
    )
    filesystem.register(mcp)
    terminal.register(mcp)
    git.register(mcp)
    process.register(mcp)
    database.register(mcp)
    monitor.register(mcp)
    deploy.register(mcp)
    network.register(mcp)
    debug.register(mcp)
    packages.register(mcp)
    intelligence.register(mcp)

    # ── Wrap tool calls with tracking ──
    _wrap_tools_with_tracking(mcp)

    logger.info(
        "MCP Nexus initialized — target=%s:%d mode=%s gateway=enabled",
        settings.ssh_host,
        settings.ssh_port,
        "local" if settings.is_localhost else "ssh",
    )
    return mcp


def _wrap_tools_with_tracking(mcp: FastMCP):
    """Inject audit logging and intelligence tracking into every tool call."""
    if not hasattr(mcp, '_tool_manager'):
        return

    manager = mcp._tool_manager
    for name, tool in list(manager._tools.items()):
        if name.startswith("nexus_"):
            continue  # don't track meta-tools to avoid recursion
        original_fn = tool.fn

        async def tracked_fn(*args, _orig=original_fn, _name=name, **kwargs) -> Any:
            start = time.monotonic()
            success = True
            error_msg = None
            try:
                result = await _orig(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                error_msg = str(e)
                raise
            finally:
                duration = (time.monotonic() - start) * 1000

                # Audit
                if _audit:
                    _audit.record(AuditEntry(
                        timestamp=time.time(),
                        tool=_name,
                        client_id="default",
                        args=kwargs,
                        success=success,
                        duration_ms=duration,
                        error=error_msg,
                    ))

                # Intelligence
                if _memory:
                    try:
                        await _memory.record(_name, kwargs, success, duration)
                    except Exception:
                        pass  # never let tracking break a tool call

        tool.fn = tracked_fn


def create_app(settings: Settings | None = None, enable_watchdog: bool = True):
    """Create a Starlette/ASGI app for streamable-http transport."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    if settings is None:
        settings = Settings()

    mcp_server = create_server(settings)
    gateway = get_gateway()

    # Watchdog background task
    watchdog_task = None
    cleanup_task = None

    async def on_startup():
        nonlocal watchdog_task, cleanup_task
        if enable_watchdog and settings.watchdog_services:
            from mcp_nexus.health.watchdog import Watchdog
            wd = Watchdog(gateway.get_owner_pool(), settings)
            watchdog_task = asyncio.create_task(wd.run())
            logger.info("Watchdog started (interval=%ds)", settings.watchdog_interval)

        # Periodic gateway cleanup
        async def _cleanup_loop():
            while True:
                await asyncio.sleep(300)  # every 5 min
                await gateway.cleanup()

        cleanup_task = asyncio.create_task(_cleanup_loop())

    async def on_shutdown():
        if watchdog_task:
            watchdog_task.cancel()
        if cleanup_task:
            cleanup_task.cancel()
        if _memory:
            _memory.close()
        await gateway.close_all()
        logger.info("MCP Nexus shut down")

    async def health_endpoint(request):
        health = await gateway.get_owner_pool().health_check()
        status_code = 200 if health["status"] == "healthy" else 503
        if _audit:
            health["audit"] = _audit.stats()
        health["gateway"] = gateway.stats()
        return JSONResponse(health, status_code=status_code)

    async def info_endpoint(request):
        from mcp_nexus import __version__
        return JSONResponse({
            "name": "mcp-nexus",
            "version": __version__,
            "transport": "streamable-http",
            "mcp_path": settings.mcp_path,
            "mode": "gateway",
            "intelligence": settings.intelligence_enabled,
            "gateway": gateway.stats(),
        })

    async def token_endpoint(request):
        """OAuth2 token endpoint — client_id=SERVER_IP, client_secret=SSH_PASSWORD."""
        if request.method != "POST":
            return JSONResponse({"error": "method_not_allowed"}, status_code=405)

        content_type = request.headers.get("content-type", "")
        if "json" in content_type:
            body = await request.json()
        else:
            body_bytes = await request.body()
            from urllib.parse import parse_qs
            parsed = parse_qs(body_bytes.decode())
            body = {k: v[0] for k, v in parsed.items()}

        grant_type = body.get("grant_type", "client_credentials")
        if grant_type != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

        client_id = body.get("client_id", "")
        client_secret = body.get("client_secret", "")
        ssh_user = body.get("ssh_user", "root")
        ssh_port = int(body.get("ssh_port", 22))

        if not client_id:
            return JSONResponse({"error": "client_id (server IP) is required"}, status_code=400)

        token = await gateway.authenticate(client_id, client_secret, ssh_user, ssh_port)
        if token is None:
            return JSONResponse({
                "error": "invalid_client",
                "message": "SSH authentication failed. Check your server IP, username, and password.",
            }, status_code=401)

        return JSONResponse(token.to_dict())

    # Add custom routes
    mcp_server._custom_starlette_routes = [
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/info", info_endpoint, methods=["GET"]),
        Route("/oauth/token", token_endpoint, methods=["POST"]),
    ]

    app = mcp_server.streamable_http_app()

    # Inject lifecycle hooks
    original_on_startup = list(app.on_startup) if hasattr(app, 'on_startup') else []
    original_on_shutdown = list(app.on_shutdown) if hasattr(app, 'on_shutdown') else []
    app.on_startup = original_on_startup + [on_startup]
    app.on_shutdown = original_on_shutdown + [on_shutdown]

    return app
