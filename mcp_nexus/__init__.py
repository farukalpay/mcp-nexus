"""MCP Nexus — Remote server management through the Model Context Protocol."""

__version__ = "1.2.0"
__author__ = "Lightcap AI"

from mcp_nexus.server import create_server

__all__ = ["create_server", "__version__"]
