"""Tests for stable tool registry metadata."""

from mcp.server.fastmcp import FastMCP

from mcp_nexus.config import Settings
from mcp_nexus.registry import apply_registry_metadata, build_tool_registry
from mcp_nexus.server import create_server


def test_registry_builds_stable_bindings():
    mcp = FastMCP("test")

    @mcp.tool()
    async def db_query(query: str) -> str:
        return query

    registry = build_tool_registry(mcp, server_instance_id="instance-1", alias_base="/mcp-nexus")
    binding = registry.tool("db_query")

    assert binding is not None
    assert binding.stable_path == "/mcp-nexus/db_query"
    assert binding.runtime_path == "/mcp-nexus/runtime/instance-1/db_query"
    assert binding.category == "database"


def test_registry_metadata_is_attached_to_tools():
    mcp = FastMCP("test")

    @mcp.tool()
    async def execute_command(command: str) -> str:
        return command

    registry = build_tool_registry(mcp, server_instance_id="instance-2", alias_base="/mcp-nexus")
    apply_registry_metadata(mcp, registry)

    tool = mcp._tool_manager._tools["execute_command"]
    assert tool.meta is not None
    assert tool.meta["nexus"]["registry_version"] == registry.registry_version
    assert tool.meta["nexus"]["stable_path"] == "/mcp-nexus/execute_command"


def test_create_server_uses_configured_streamable_http_path():
    settings = Settings()
    settings.mcp_path = "/mcp/nexus"
    server = create_server(settings)
    assert server.settings.streamable_http_path == "/mcp/nexus"
