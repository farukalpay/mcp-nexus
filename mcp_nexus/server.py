"""MCP Nexus server — registers tools, registry state, and HTTP diagnostics."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import time
from http import HTTPStatus
from typing import Any, cast
from uuid import uuid4

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl, BaseModel

from mcp_nexus.auth.oauth import GatewayOAuthProvider
from mcp_nexus.config import Settings
from mcp_nexus.gateway import GatewayManager
from mcp_nexus.intelligence.memory import MemoryEngine
from mcp_nexus.landing import render_mcp_entry_page
from mcp_nexus.middleware.audit import AuditEntry, AuditLog
from mcp_nexus.middleware.rate_limit import RateLimiter
from mcp_nexus.registry import ToolRegistry, apply_registry_metadata, build_tool_registry
from mcp_nexus.results import ArtifactManager, ToolExecutionContext
from mcp_nexus.telemetry import (
    RequestTrace,
    SessionStateStore,
    get_request_trace,
    reset_request_trace,
    set_request_trace,
)
from mcp_nexus.transport.ssh import SSHPool

logger = logging.getLogger(__name__)

# Global state shared across tools and routes.
_gateway: GatewayManager | None = None
_settings: Settings | None = None
_memory: MemoryEngine | None = None
_audit: AuditLog | None = None
_rate_limiter: RateLimiter | None = None
_tool_registry: ToolRegistry | None = None
_session_store: SessionStateStore | None = None
_artifacts: ArtifactManager | None = None
_server_instance_id: str | None = None
_oauth_provider: GatewayOAuthProvider | None = None

# Per-request pool context (set by request middleware).
_current_pool: contextvars.ContextVar[SSHPool | None] = contextvars.ContextVar("_current_pool", default=None)


def get_pool() -> SSHPool:
    """Get the SSH pool for the current request."""
    pool = _current_pool.get()
    if pool is not None:
        return pool

    assert _gateway is not None, "Gateway not initialized — call create_server() first"
    return _gateway.get_owner_pool()


def set_current_pool(pool: SSHPool):
    """Set the SSH pool for the current request context."""
    return _current_pool.set(pool)


def reset_current_pool(token):
    _current_pool.reset(token)


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


def get_registry() -> ToolRegistry:
    assert _tool_registry is not None, "Tool registry not initialized"
    return _tool_registry


def get_session_store() -> SessionStateStore:
    assert _session_store is not None, "Session store not initialized"
    return _session_store


def get_artifacts() -> ArtifactManager:
    assert _artifacts is not None, "Artifact manager not initialized"
    return _artifacts


def get_server_instance_id() -> str:
    assert _server_instance_id is not None, "Server instance not initialized"
    return _server_instance_id


def get_oauth_provider() -> GatewayOAuthProvider | None:
    return _oauth_provider


def tool_context(tool_name: str) -> ToolExecutionContext:
    """Return stable execution context for a tool invocation."""
    registry = get_registry()
    binding = registry.tool(tool_name)
    trace = get_request_trace()
    backend = get_pool().backend_metadata()
    return ToolExecutionContext(
        tool_name=tool_name,
        stable_name=binding.stable_name if binding else tool_name,
        resolved_runtime_id=binding.resolved_runtime_id if binding else tool_name,
        server_instance_id=registry.server_instance_id,
        registry_version=registry.registry_version,
        request_id=trace.request_id if trace else "offline",
        trace_id=trace.trace_id if trace else "offline",
        session_id=trace.session_id if trace else None,
        backend_kind=str(backend["backend_kind"]),
        backend_instance=str(backend["backend_instance"]),
    )


def create_server(settings: Settings | None = None) -> FastMCP:
    """Create and configure the MCP server with all tools."""
    global _gateway, _settings, _memory, _audit, _rate_limiter, _tool_registry, _session_store, _artifacts
    global _server_instance_id, _oauth_provider

    if settings is None:
        settings = Settings()
    _settings = settings
    _server_instance_id = uuid4().hex
    _gateway = GatewayManager(settings)
    _oauth_provider = None

    audit_log_file = settings.audit_log_file or f"{settings.expanded_path(settings.data_dir)}/audit.jsonl"
    _audit = AuditLog(max_entries=5000, log_file=audit_log_file)
    _rate_limiter = RateLimiter(rpm=settings.rate_limit_rpm, burst=settings.rate_limit_burst)
    _session_store = SessionStateStore()
    _artifacts = ArtifactManager(settings.expanded_path(settings.artifact_root))

    if settings.intelligence_enabled:
        _memory = MemoryEngine(data_dir=settings.data_dir)
        _memory.open()
        logger.info("Intelligence engine enabled — data at %s", settings.data_dir)

    auth_settings = None
    if settings.oauth_ready:
        issuer_url = AnyHttpUrl(settings.oauth_issuer_url)
        resource_server_url = AnyHttpUrl(settings.oauth_resource_server_url)
        service_documentation_url = (
            AnyHttpUrl(settings.oauth_service_documentation_url) if settings.oauth_service_documentation_url else None
        )
        _oauth_provider = GatewayOAuthProvider(settings, _gateway)
        auth_settings = AuthSettings(
            issuer_url=issuer_url,
            service_documentation_url=service_documentation_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=settings.oauth_valid_scopes,
                default_scopes=settings.oauth_default_scopes or settings.oauth_required_scopes,
            ),
            required_scopes=settings.oauth_required_scopes,
            resource_server_url=resource_server_url,
        )

    mcp = FastMCP(
        "nexus",
        instructions=(
            "MCP Nexus provides remote server management with explicit registry metadata and session diagnostics. "
            "Use tools to inspect files, execute commands, query databases, manage services, and deploy code. "
            "Registry metadata includes stable tool bindings, server instance id, and registry version. "
            "Prefer db_profiles and db_use to select database backends without exposing secrets in tool arguments."
        ),
        host=settings.host,
        port=settings.port,
        streamable_http_path=settings.mcp_path,
        auth=auth_settings,
        auth_server_provider=_oauth_provider,
    )

    from mcp_nexus.tools import (
        database,
        debug,
        deploy,
        filesystem,
        git,
        intelligence,
        logs,
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
    logs.register(mcp)

    _tool_registry = build_tool_registry(
        mcp,
        server_instance_id=_server_instance_id,
        alias_base=settings.tool_alias_base,
    )
    apply_registry_metadata(mcp, _tool_registry)
    _register_registry_resources(mcp)
    _wrap_tools_with_tracking(mcp)

    logger.info(
        "MCP Nexus initialized — target=%s:%d mode=%s registry=%s server_instance=%s",
        settings.ssh_host,
        settings.ssh_port,
        "local" if settings.is_localhost else "ssh",
        _tool_registry.registry_version,
        _tool_registry.server_instance_id,
    )
    return mcp


def _register_registry_resources(mcp: FastMCP):
    """Expose inspectable registry/session resources through MCP resources."""

    @mcp.resource("nexus://tool-registry", name="tool_registry", description="Current Nexus tool registry snapshot")
    async def tool_registry_resource() -> str:
        return json.dumps(get_registry().to_dict(), indent=2)

    @mcp.resource("nexus://version", name="version", description="Server version and binding metadata")
    async def version_resource() -> str:
        from mcp_nexus import __version__

        registry = get_registry()
        settings = get_settings()
        return json.dumps(
            {
                "name": "mcp-nexus",
                "version": __version__,
                "server_instance_id": registry.server_instance_id,
                "registry_version": registry.registry_version,
                "bind": {
                    "host": settings.host,
                    "port": settings.port,
                    "mcp_path": settings.mcp_path,
                },
                "transport": "streamable-http",
            },
            indent=2,
        )

    @mcp.resource("nexus://session/{session_id}", name="session", description="Session-scoped Nexus state snapshot")
    async def session_resource(session_id: str) -> str:
        state = get_session_store().get_session(session_id)
        return json.dumps(
            {
                "session_id": session_id,
                "state": state,
                "active": session_id in _active_session_ids(cast(Any, getattr(mcp, "_session_manager", None))),
            },
            indent=2,
        )


def _result_payload(result: Any) -> dict[str, Any] | None:
    if isinstance(result, BaseModel):
        return cast(dict[str, Any], result.model_dump(exclude_none=True))
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def _wrap_tools_with_tracking(mcp: FastMCP):
    """Inject audit logging and intelligence tracking into every tool call."""
    manager = getattr(mcp, "_tool_manager", None)
    if manager is None:
        return

    for name, tool in list(manager._tools.items()):
        if name.startswith("nexus_"):
            continue
        original_fn = tool.fn

        async def tracked_fn(*args, _orig=original_fn, _name=name, **kwargs) -> Any:
            start = time.monotonic()
            success = True
            error_msg = None
            audit_metadata = None
            trace = get_request_trace()
            try:
                result = await _orig(*args, **kwargs)
                payload = _result_payload(result)
                if isinstance(payload, dict):
                    audit_metadata = {
                        key: payload[key]
                        for key in (
                            "usage",
                            "resource_usage",
                            "profile",
                            "error_code",
                            "error_stage",
                            "request_id",
                            "trace_id",
                            "session_id",
                            "backend_kind",
                            "backend_instance",
                            "server_instance_id",
                            "registry_version",
                        )
                        if key in payload
                    } or None
                return result
            except Exception as exc:
                success = False
                error_msg = str(exc)
                raise
            finally:
                duration = (time.monotonic() - start) * 1000
                session_id = trace.session_id if trace else None
                if _audit:
                    _audit.record(
                        AuditEntry(
                            timestamp=time.time(),
                            tool=_name,
                            client_id=(trace.session_id if trace and trace.session_id else "default"),
                            args=kwargs,
                            success=success,
                            duration_ms=duration,
                            error=error_msg,
                            metadata=audit_metadata,
                            request_id=trace.request_id if trace else None,
                            trace_id=trace.trace_id if trace else None,
                            session_id=session_id,
                            backend_kind=tool_context(_name).backend_kind,
                            backend_instance=tool_context(_name).backend_instance,
                            registry_version=get_registry().registry_version,
                            server_instance_id=get_registry().server_instance_id,
                        )
                    )

                get_session_store().note_tool_result(session_id, _name, success, error_message=error_msg)

                if _memory:
                    try:
                        await _memory.record(_name, kwargs, success, duration)
                    except Exception:
                        pass

        tool.fn = tracked_fn


def _protected_resource_metadata_enabled(mcp_server: FastMCP) -> bool:
    auth = getattr(mcp_server.settings, "auth", None)
    return bool(auth and getattr(auth, "resource_server_url", None))


def _current_transport_metadata(mcp_server: FastMCP, settings: Settings) -> dict[str, Any]:
    trace = get_request_trace()
    auth_settings = getattr(mcp_server.settings, "auth", None)
    protected_resource_metadata_url = None
    authorization_server_metadata_url = None
    if auth_settings and auth_settings.resource_server_url:
        from mcp.server.auth.routes import build_resource_metadata_url

        protected_resource_metadata_url = str(build_resource_metadata_url(auth_settings.resource_server_url))
        authorization_server_metadata_url = (
            f"{str(auth_settings.issuer_url).rstrip('/')}/.well-known/oauth-authorization-server"
        )
    auth_metadata = {
        "enabled": bool(auth_settings),
        "issuer_url": str(auth_settings.issuer_url) if auth_settings else None,
        "resource_server_url": str(auth_settings.resource_server_url) if auth_settings else None,
        "required_scopes": list(auth_settings.required_scopes or []) if auth_settings else [],
        "authorization_server_metadata_url": authorization_server_metadata_url,
        "protected_resource_metadata_url": protected_resource_metadata_url,
        "consent_url": settings.oauth_consent_url if auth_settings else None,
        "legacy_token_endpoint": (
            f"{settings.oauth_issuer_url.rstrip('/')}/oauth/token"
            if auth_settings and settings.oauth_issuer_url
            else None
        ),
        "client_registration_enabled": bool(
            auth_settings
            and auth_settings.client_registration_options
            and auth_settings.client_registration_options.enabled
        ),
        "static_client_enabled": settings.oauth_static_client_enabled if auth_settings else False,
        "static_client_id": (
            settings.oauth_client_id if auth_settings and settings.oauth_static_client_enabled else None
        ),
        "static_client_redirect_uris": (
            list(settings.oauth_client_redirect_uris) if auth_settings and settings.oauth_static_client_enabled else []
        ),
    }
    return {
        "transport": trace.transport if trace else "streamable-http",
        "bind": {
            "host": settings.host,
            "port": settings.port,
            "mcp_path": settings.mcp_path,
            "mcp_path_aliases": settings.mcp_path_aliases,
        },
        "auth_mode": trace.auth_mode if trace else "none",
        "auth": auth_metadata,
        "forwarded_headers": trace.forwarded_headers if trace else {},
        "forwarded_headers_supported": settings.forwarded_headers,
        "protected_resource_metadata": _protected_resource_metadata_enabled(mcp_server),
    }


def _active_session_ids(session_manager: Any | None) -> set[str]:
    if session_manager is None:
        return set()
    server_instances = getattr(session_manager, "_server_instances", None)
    if not isinstance(server_instances, dict):
        return set()
    return set(server_instances.keys())


def _typed_session_error(session_id: str) -> dict[str, Any]:
    registry = get_registry()
    state = get_session_store().get_session(session_id)
    error_code = "SESSION_NOT_FOUND"
    message = "Session is unknown to this server instance."

    if state and state.get("status") == "closed":
        error_code = "SESSION_CLOSED"
        message = "Session existed earlier but its live runtime binding is no longer active."

    previous_registry_version = state.get("registry_version") if state else None
    if previous_registry_version and previous_registry_version != registry.registry_version:
        error_code = "REGISTRY_VERSION_CHANGED"
        message = (
            "tool disappeared because registry_version changed "
            f"from {previous_registry_version} to {registry.registry_version}"
        )

    return {
        "jsonrpc": "2.0",
        "id": "server-error",
        "error": {
            "code": -32600,
            "message": "Session not found",
            "data": {
                "ok": False,
                "error_code": error_code,
                "error_stage": "transport",
                "message": message,
                "session_id": session_id,
                "server_instance_id": registry.server_instance_id,
                "registry_version": registry.registry_version,
                "previous_registry_version": previous_registry_version,
                "session_state": state,
            },
        },
    }


def _is_mcp_request(path: str, settings: Settings) -> bool:
    normalized_path = path.rstrip("/") or "/"
    normalized_mcp_path = settings.mcp_path.rstrip("/") or "/"
    return normalized_path == normalized_mcp_path


def _resolve_mcp_path(path: str, settings: Settings) -> str:
    normalized_path = path.rstrip("/") or "/"
    for alias in settings.mcp_path_aliases:
        normalized_alias = alias.rstrip("/") or "/"
        if normalized_path == normalized_alias:
            return settings.mcp_path
    return path


def _is_browser_html_request(method: str, accept_header: str) -> bool:
    if method.upper() != "GET":
        return False
    accept = accept_header.lower()
    return "text/html" in accept and "application/json" not in accept


class NexusRequestContextMiddleware:
    """Inject request/session metadata and typed transport errors."""

    def __init__(self, app, *, settings: Settings, mcp_server: FastMCP):
        self.app = app
        self.settings = settings
        self.mcp_server = mcp_server

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.datastructures import Headers, MutableHeaders
        from starlette.responses import JSONResponse

        headers = Headers(scope=scope)
        resolved_scope = scope
        original_path = scope.get("path", "")
        path = _resolve_mcp_path(original_path, self.settings)
        if path != original_path:
            resolved_scope = dict(scope)
            resolved_scope["path"] = path
            resolved_scope["raw_path"] = path.encode()
        if _is_mcp_request(path, self.settings) and _is_browser_html_request(
            scope.get("method", "GET"),
            headers.get("accept", ""),
        ):
            response = render_mcp_entry_page(self.settings)
            await response(scope, receive, send)
            return
        request_id = headers.get("x-request-id") or uuid4().hex
        trace_id = headers.get("x-trace-id") or request_id
        session_id = headers.get("mcp-session-id")
        auth_header = headers.get("authorization", "")
        auth_mode = "bearer" if auth_header.lower().startswith("bearer ") else "none"
        forwarded_headers = {name: value for name in self.settings.forwarded_headers if (value := headers.get(name))}
        transport = "streamable-http" if _is_mcp_request(path, self.settings) else "http"

        pool_token = None
        if auth_mode == "bearer":
            token_value = auth_header.split(None, 1)[1].strip() if " " in auth_header else ""
            pool = get_gateway().get_pool_for_token(token_value) if token_value else None
            if pool is not None:
                pool_token = set_current_pool(pool)
                auth_mode = "gateway-token"
            else:
                auth_mode = "invalid-bearer"

        active_session_ids = _active_session_ids(getattr(self.mcp_server, "_session_manager", None))
        if _is_mcp_request(path, self.settings) and session_id and session_id not in active_session_ids:
            response = JSONResponse(_typed_session_error(session_id), status_code=HTTPStatus.NOT_FOUND)
            await response(scope, receive, send)
            if pool_token is not None:
                reset_current_pool(pool_token)
            return

        trace_token = set_request_trace(
            RequestTrace(
                request_id=request_id,
                trace_id=trace_id,
                session_id=session_id,
                transport=transport,
                auth_mode=auth_mode,
                forwarded_headers=forwarded_headers,
            )
        )

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_session_id = response_headers.get("mcp-session-id") or session_id
                if _is_mcp_request(path, self.settings) and response_session_id:
                    get_session_store().touch(
                        response_session_id,
                        request_id=request_id,
                        trace_id=trace_id,
                        transport=transport,
                        registry_version=get_registry().registry_version,
                        server_instance_id=get_registry().server_instance_id,
                    )
                response_headers["x-request-id"] = request_id
                response_headers["x-trace-id"] = trace_id
                response_headers["x-nexus-server-instance-id"] = get_registry().server_instance_id
                response_headers["x-nexus-registry-version"] = get_registry().registry_version
            await send(message)

        try:
            await self.app(resolved_scope, receive, send_wrapper)
        finally:
            get_session_store().sync_active(
                _active_session_ids(getattr(self.mcp_server, "_session_manager", None)),
                registry_version=get_registry().registry_version,
                server_instance_id=get_registry().server_instance_id,
            )
            reset_request_trace(trace_token)
            if pool_token is not None:
                reset_current_pool(pool_token)


def create_app(settings: Settings | None = None, enable_watchdog: bool = True):
    """Create a Starlette/ASGI app for streamable-http transport."""
    from starlette.responses import JSONResponse, RedirectResponse
    from starlette.routing import Route

    if settings is None:
        settings = Settings()

    mcp_server = create_server(settings)
    gateway = get_gateway()

    watchdog_task = None
    cleanup_task = None

    async def on_startup():
        nonlocal watchdog_task, cleanup_task
        if enable_watchdog and settings.watchdog_services:
            from mcp_nexus.health.watchdog import Watchdog

            wd = Watchdog(gateway.get_owner_pool(), settings)
            watchdog_task = asyncio.create_task(wd.run())
            logger.info("Watchdog started (interval=%ds)", settings.watchdog_interval)

        async def cleanup_loop():
            while True:
                await asyncio.sleep(300)
                await gateway.cleanup()

        cleanup_task = asyncio.create_task(cleanup_loop())

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
        pool_health = await gateway.get_owner_pool().health_check()
        status_code = 200 if pool_health["status"] == "healthy" else 503
        health = {
            **pool_health,
            "gateway": gateway.stats(),
            "registry": {
                "server_instance_id": get_registry().server_instance_id,
                "registry_version": get_registry().registry_version,
                "tool_count": len(get_registry().tools),
            },
            "transport": _current_transport_metadata(mcp_server, settings),
            "sessions": {
                "active": len(_active_session_ids(getattr(mcp_server, "_session_manager", None))),
                "tracked": len(get_session_store().list_sessions()),
            },
        }
        if _audit:
            health["audit"] = _audit.stats()
        return JSONResponse(health, status_code=status_code)

    async def ready_endpoint(request):
        pool_health = await gateway.get_owner_pool().health_check()
        ready = pool_health["status"] == "healthy" and get_registry().tool("execute_command") is not None
        return JSONResponse(
            {
                "ready": ready,
                "checks": {
                    "gateway": pool_health["status"],
                    "registry": "ready" if get_registry().tools else "missing",
                },
                "server_instance_id": get_registry().server_instance_id,
                "registry_version": get_registry().registry_version,
            },
            status_code=200 if ready else 503,
        )

    async def version_endpoint(request):
        from mcp_nexus import __version__

        return JSONResponse(
            {
                "name": "mcp-nexus",
                "version": __version__,
                "server_instance_id": get_registry().server_instance_id,
                "registry_version": get_registry().registry_version,
                **_current_transport_metadata(mcp_server, settings),
            }
        )

    async def info_endpoint(request):
        from mcp_nexus import __version__

        return JSONResponse(
            {
                "name": "mcp-nexus",
                "version": __version__,
                "mode": "gateway",
                "gateway": gateway.stats(),
                "registry": {
                    "server_instance_id": get_registry().server_instance_id,
                    "registry_version": get_registry().registry_version,
                },
                **_current_transport_metadata(mcp_server, settings),
            }
        )

    async def tool_registry_endpoint(request):
        registry = get_registry()
        return JSONResponse(
            {
                **registry.to_dict(),
                "sessions": {
                    "active": len(_active_session_ids(getattr(mcp_server, "_session_manager", None))),
                },
            }
        )

    async def sessions_endpoint(request):
        return JSONResponse({"sessions": get_session_store().list_sessions()})

    async def session_endpoint(request):
        session_id = request.path_params["session_id"]
        state = get_session_store().get_session(session_id)
        if state is None:
            return JSONResponse(
                {
                    "ok": False,
                    "error_code": "SESSION_NOT_FOUND",
                    "error_stage": "lookup",
                    "session_id": session_id,
                    "server_instance_id": get_registry().server_instance_id,
                    "registry_version": get_registry().registry_version,
                },
                status_code=404,
            )
        return JSONResponse(
            {
                "session": state,
                "active": session_id in _active_session_ids(getattr(mcp_server, "_session_manager", None)),
            }
        )

    async def token_endpoint(request):
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
            return JSONResponse(
                {
                    "error": "invalid_client",
                    "message": "SSH authentication failed. Check your server IP, username, and password.",
                },
                status_code=401,
            )

        return JSONResponse(token.to_dict())

    async def oauth_consent_get_endpoint(request):
        provider = get_oauth_provider()
        if provider is None:
            return JSONResponse({"error": "oauth_not_enabled"}, status_code=404)

        request_id = request.query_params.get("request_id", "")
        return provider.render_consent_page(request_id)

    async def oauth_consent_post_endpoint(request):
        provider = get_oauth_provider()
        if provider is None:
            return JSONResponse({"error": "oauth_not_enabled"}, status_code=404)

        form = await request.form()
        request_id = str(form.get("request_id", ""))
        decision = str(form.get("decision", "deny"))
        ssh_host = str(form.get("ssh_host", settings.ssh_host)).strip()
        ssh_user = str(form.get("ssh_user", settings.ssh_user or "root")).strip() or "root"
        ssh_password = str(form.get("ssh_password", ""))
        ssh_port_raw = str(form.get("ssh_port", settings.ssh_port or 22)).strip()
        try:
            ssh_port = int(ssh_port_raw or settings.ssh_port or 22)
        except ValueError:
            return provider.render_consent_page(request_id, error_message="SSH port must be a valid integer.")

        try:
            redirect_url = await provider.complete_authorization(
                request_id,
                decision=decision,
                ssh_host=ssh_host,
                ssh_user=ssh_user,
                ssh_port=ssh_port,
                ssh_password=ssh_password,
            )
        except KeyError:
            return provider.render_consent_page(
                request_id,
                error_message="This authorization request expired. Retry Connect from ChatGPT.",
            )
        except ValueError as exc:
            return provider.render_consent_page(request_id, error_message=str(exc))

        return RedirectResponse(url=redirect_url, status_code=302, headers={"Cache-Control": "no-store"})

    setattr(
        mcp_server,
        "_custom_starlette_routes",
        [
            Route("/health", health_endpoint, methods=["GET"]),
            Route("/ready", ready_endpoint, methods=["GET"]),
            Route("/version", version_endpoint, methods=["GET"]),
            Route("/info", info_endpoint, methods=["GET"]),
            Route("/tool-registry", tool_registry_endpoint, methods=["GET"]),
            Route("/sessions", sessions_endpoint, methods=["GET"]),
            Route("/session/{session_id}", session_endpoint, methods=["GET"]),
            Route("/oauth/token", token_endpoint, methods=["POST"]),
            Route(settings.oauth_consent_path, oauth_consent_get_endpoint, methods=["GET"]),
            Route(settings.oauth_consent_path, oauth_consent_post_endpoint, methods=["POST"]),
        ],
    )

    app = mcp_server.streamable_http_app()
    app.add_middleware(NexusRequestContextMiddleware, settings=settings, mcp_server=mcp_server)

    existing_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(inner_app):
        async with existing_lifespan(inner_app):
            await on_startup()
            try:
                yield
            finally:
                await on_shutdown()

    app.router.lifespan_context = lifespan

    return app
