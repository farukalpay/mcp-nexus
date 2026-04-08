"""CLI entry point: python -m mcp_nexus."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import uvicorn

from mcp_nexus.config import Settings
from mcp_nexus.server import create_app, create_server


def main():
    parser = argparse.ArgumentParser(
        prog="mcp-nexus",
        description="MCP Nexus — Remote server management via Model Context Protocol",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser("serve", help="Start the MCP server")
    serve_p.add_argument("--host", default=None, help="Bind host (default: from .env)")
    serve_p.add_argument("--port", type=int, default=None, help="Bind port (default: from .env)")
    serve_p.add_argument("--transport", choices=["streamable-http", "stdio"], default="streamable-http")
    serve_p.add_argument("--no-watchdog", action="store_true", help="Disable health watchdog")
    serve_p.add_argument("--log-level", default=None, choices=["debug", "info", "warning", "error"])

    sub.add_parser("health", help="Check server health")
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
    app = create_app(settings, enable_watchdog=enable_watchdog)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


async def _serve_stdio(settings: Settings):
    server = create_server(settings)
    await server.run_stdio_async()


async def _health_check():
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
    except Exception as exc:
        print(f"SSH connection: FAILED — {exc}")
        sys.exit(1)
    finally:
        await pool.close()


if __name__ == "__main__":
    main()
