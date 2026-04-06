"""CLI entry point: python -m mcp_nexus"""

import argparse
import asyncio
import logging
import sys

from mcp_nexus.config import Settings
from mcp_nexus.server import create_server


def main():
    parser = argparse.ArgumentParser(
        prog="mcp-nexus",
        description="MCP Nexus — Remote server management via Model Context Protocol",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── serve ──
    serve_p = sub.add_parser("serve", help="Start the MCP server")
    serve_p.add_argument("--host", default=None, help="Bind host (default: from .env)")
    serve_p.add_argument("--port", type=int, default=None, help="Bind port (default: from .env)")
    serve_p.add_argument("--transport", choices=["streamable-http", "stdio"], default="streamable-http")
    serve_p.add_argument("--no-watchdog", action="store_true", help="Disable health watchdog")
    serve_p.add_argument("--log-level", default=None, choices=["debug", "info", "warning", "error"])

    # ── health ──
    sub.add_parser("health", help="Check server health")

    # ── version ──
    sub.add_parser("version", help="Show version")

    args = parser.parse_args()

    if args.command == "version":
        from mcp_nexus import __version__
        print(f"mcp-nexus {__version__}")
        return

    if args.command == "health":
        asyncio.run(_health_check())
        return

    if args.command == "serve":
        settings = Settings()
        if args.host:
            settings.host = args.host
        if args.port:
            settings.port = args.port
        if args.log_level:
            settings.log_level = args.log_level

        logging.basicConfig(
            level=getattr(logging, settings.log_level.upper()),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        if args.transport == "stdio":
            asyncio.run(_serve_stdio(settings))
        else:
            _serve_http(settings, enable_watchdog=not args.no_watchdog)


def _serve_http(settings: Settings, enable_watchdog: bool = True):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from mcp_nexus.server import get_gateway

    mcp = create_server(settings)
    gateway = get_gateway()

    # ── Gateway OAuth + health routes ──
    async def token_endpoint(request: Request):
        """OAuth2 token: client_id=SERVER_IP, client_secret=SSH_PASSWORD."""
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

    async def health_endpoint(request: Request):
        health = await gateway.get_owner_pool().health_check()
        health["gateway"] = gateway.stats()
        status_code = 200 if health["status"] == "healthy" else 503
        return JSONResponse(health, status_code=status_code)

    async def info_endpoint(request: Request):
        from mcp_nexus import __version__
        return JSONResponse({
            "name": "mcp-nexus",
            "version": __version__,
            "mode": "gateway",
            "gateway": gateway.stats(),
        })

    mcp._custom_starlette_routes = [
        Route("/oauth/token", token_endpoint, methods=["POST"]),
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/info", info_endpoint, methods=["GET"]),
    ]

    # Start watchdog in background if enabled
    if enable_watchdog:
        import threading

        from mcp_nexus.server import get_pool

        def _run_watchdog():
            from mcp_nexus.health.watchdog import Watchdog
            loop = asyncio.new_event_loop()
            wd = Watchdog(get_pool(), settings)
            loop.run_until_complete(wd.run())

        t = threading.Thread(target=_run_watchdog, daemon=True)
        t.start()

    mcp.run(transport="streamable-http", mount_path=settings.mcp_path)


async def _serve_stdio(settings: Settings):

    server = create_server(settings)
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def _health_check():
    from mcp_nexus.config import Settings
    from mcp_nexus.transport.ssh import SSHPool

    settings = Settings()
    pool = SSHPool(settings)
    try:
        conn = await pool.acquire()
        result = await conn.run("echo ok", timeout=5)
        if result.strip() == "ok":
            print("SSH connection: OK")
        else:
            print("SSH connection: DEGRADED")
            sys.exit(1)
    except Exception as e:
        print(f"SSH connection: FAILED — {e}")
        sys.exit(1)
    finally:
        await pool.close()


if __name__ == "__main__":
    main()
