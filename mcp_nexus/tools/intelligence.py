"""Intelligence tools — recall context, view insights, manage preferences."""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from mcp_nexus.server import get_memory


def register(mcp: FastMCP):

    @mcp.tool()
    async def nexus_recall() -> str:
        """Recall what you were working on — recent actions, session context, and errors.

        Use this at the start of a conversation to pick up where you left off.
        """
        memory = get_memory()
        if not memory:
            return json.dumps({"status": "intelligence disabled"})

        ctx = await memory.get_context()
        prefs = await memory.get_preferences()
        if prefs:
            ctx["preferences"] = prefs
        return json.dumps(ctx, indent=2)

    @mcp.tool()
    async def nexus_insights() -> str:
        """View usage analytics — top tools, focus areas, error rates, and detected workflows.

        Helps understand how you use the server and identify automation opportunities.
        """
        memory = get_memory()
        if not memory:
            return json.dumps({"status": "intelligence disabled"})

        insights = await memory.get_insights()
        workflows = await memory.get_workflows()
        if workflows:
            insights["detected_workflows"] = workflows
        return json.dumps(insights, indent=2)

    @mcp.tool()
    async def nexus_suggest(current_tool: str = "") -> str:
        """Get smart suggestions for what to do next based on your usage patterns.

        Args:
            current_tool: The tool you just used (auto-filled if omitted).
        """
        memory = get_memory()
        if not memory:
            return json.dumps({"status": "intelligence disabled"})

        suggestions = await memory.suggest_next(current_tool) if current_tool else []
        ctx = await memory.get_context()
        return json.dumps({
            "next_tools": suggestions,
            "recent_context": ctx.get("recent_actions", [])[:3],
        }, indent=2)

    @mcp.tool()
    async def nexus_preferences(action: str = "list", key: str = "", value: str = "") -> str:
        """View or set user preferences that the system has learned.

        Args:
            action: "list" to view all, "set" to manually set a preference, "clear" to reset all memory.
            key: Preference key (for set action). Common keys: default_repo, working_directory, watched_service.
            value: Preference value (for set action).
        """
        memory = get_memory()
        if not memory:
            return json.dumps({"status": "intelligence disabled"})

        if action == "set" and key and value:
            await memory.set_preference(key, value)
            return json.dumps({"status": "ok", "key": key, "value": value})

        if action == "clear":
            await memory.clear()
            return json.dumps({"status": "memory cleared"})

        prefs = await memory.get_preferences()
        return json.dumps(prefs, indent=2)
