"""SQLite-based memory engine — learns from every tool call."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

from mcp_nexus.catalog import category_for_tool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,
    args_summary TEXT,
    success INTEGER NOT NULL DEFAULT 1,
    duration_ms REAL,
    session_id INTEGER,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    times_seen INTEGER NOT NULL DEFAULT 1,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    last_activity REAL NOT NULL,
    tool_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS tool_sequences (
    prev_tool TEXT NOT NULL,
    next_tool TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (prev_tool, next_tool)
);

CREATE INDEX IF NOT EXISTS idx_interactions_ts ON interactions(ts);
CREATE INDEX IF NOT EXISTS idx_interactions_tool ON interactions(tool);
CREATE INDEX IF NOT EXISTS idx_interactions_session ON interactions(session_id);
"""

# Idle gap (seconds) that starts a new session.
_SESSION_GAP = 1800  # 30 min


class MemoryEngine:
    """Lightweight memory that learns from server interactions.

    Every tool call is recorded.  Over time the engine auto-detects:
    - Preferred paths, repos, services, and working directories
    - Common tool sequences (e.g. git_status -> git_diff -> git_commit)
    - Session context so the AI can say "you were last working on ..."
    """

    def __init__(self, data_dir: str = "~/.mcp-nexus"):
        self._data_dir = Path(data_dir).expanduser()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / "memory.db"
        self._conn: sqlite3.Connection | None = None
        self._current_session: int | None = None
        self._last_tool: str | None = None
        self._last_activity: float = 0

    # ── lifecycle ──

    def open(self):
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    def close(self):
        if self._conn:
            self._finalize_session()
            self._conn.close()
            self._conn = None

    # ── recording ──

    async def record(self, tool: str, args: dict[str, Any], success: bool, duration_ms: float):
        """Record a tool invocation — runs DB writes in a thread."""
        await asyncio.to_thread(self._record_sync, tool, args, success, duration_ms)

    def _record_sync(self, tool: str, args: dict[str, Any], success: bool, duration_ms: float):
        if not self._conn:
            return
        now = time.time()
        sid = self._ensure_session(now)

        summary = self._summarize_args(args)
        self._conn.execute(
            "INSERT INTO interactions (tool, args_summary, success, duration_ms, session_id, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tool, summary, int(success), duration_ms, sid, now),
        )

        # Update session
        self._conn.execute(
            "UPDATE sessions SET last_activity=?, tool_count=tool_count+1 WHERE id=?",
            (now, sid),
        )

        # Track tool sequences
        if self._last_tool and self._last_tool != tool:
            self._conn.execute(
                "INSERT INTO tool_sequences (prev_tool, next_tool, count) VALUES (?, ?, 1) "
                "ON CONFLICT(prev_tool, next_tool) DO UPDATE SET count=count+1",
                (self._last_tool, tool),
            )

        self._last_tool = tool
        self._last_activity = now

        # Learn preferences from arguments
        self._learn_preferences(tool, args, now)

        self._conn.commit()

    # ── preferences ──

    _PREFERENCE_KEYS = {
        "repo_path": "default_repo",
        "path": "working_directory",
        "cwd": "working_directory",
        "service_name": "watched_service",
        "venv_path": "default_venv",
        "backup_dir": "backup_directory",
    }

    def _learn_preferences(self, tool: str, args: dict[str, Any], now: float):
        if not self._conn:
            return
        for arg_key, pref_key in self._PREFERENCE_KEYS.items():
            value = args.get(arg_key)
            if not value or not isinstance(value, str):
                continue
            # Skip very generic values
            if value in ("/", ".", "/tmp", ""):
                continue

            full_key = pref_key
            existing = self._conn.execute(
                "SELECT value, times_seen FROM preferences WHERE key=?", (full_key,)
            ).fetchone()

            if existing and existing["value"] == value:
                new_count = existing["times_seen"] + 1
                confidence = min(1.0, 0.3 + new_count * 0.1)
                self._conn.execute(
                    "UPDATE preferences SET times_seen=?, confidence=?, last_seen=? WHERE key=?",
                    (new_count, confidence, now, full_key),
                )
            elif existing:
                # Different value — only replace if current is more common
                if existing["times_seen"] <= 2:
                    self._conn.execute(
                        "UPDATE preferences SET value=?, times_seen=1, confidence=0.3, last_seen=? WHERE key=?",
                        (value, now, full_key),
                    )
            else:
                self._conn.execute(
                    "INSERT INTO preferences (key, value, confidence, times_seen, first_seen, last_seen) "
                    "VALUES (?, ?, 0.3, 1, ?, ?)",
                    (full_key, value, now, now),
                )

    # ── queries ──

    async def get_context(self) -> dict[str, Any]:
        """What has the user been working on?"""
        return await asyncio.to_thread(self._get_context_sync)

    def _get_context_sync(self) -> dict[str, Any]:
        if not self._conn:
            return {"status": "memory not initialized"}

        ctx: dict[str, Any] = {}

        # Current / last session summary
        row = self._conn.execute(
            "SELECT id, started_at, last_activity, tool_count FROM sessions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            elapsed = time.time() - row["last_activity"]
            if elapsed < _SESSION_GAP:
                ctx["current_session"] = {
                    "active": True,
                    "tools_used": row["tool_count"],
                    "duration_min": round((row["last_activity"] - row["started_at"]) / 60, 1),
                }
            else:
                ctx["last_session"] = {
                    "ended_ago_min": round(elapsed / 60, 1),
                    "tools_used": row["tool_count"],
                }

            # Recent tools in this session
            tools = self._conn.execute(
                "SELECT tool, args_summary, success FROM interactions WHERE session_id=? ORDER BY ts DESC LIMIT 10",
                (row["id"],),
            ).fetchall()
            ctx["recent_actions"] = [
                {"tool": t["tool"], "args": self._parse_args_summary(t["args_summary"]), "ok": bool(t["success"])}
                for t in tools
            ]

        # Recent errors
        errors = self._conn.execute(
            "SELECT tool, args_summary, ts FROM interactions WHERE success=0 ORDER BY ts DESC LIMIT 5"
        ).fetchall()
        if errors:
            ctx["recent_errors"] = [
                {
                    "tool": e["tool"],
                    "args": self._parse_args_summary(e["args_summary"]),
                    "ago_min": round((time.time() - e["ts"]) / 60, 1),
                }
                for e in errors
            ]

        return ctx

    async def get_preferences(self) -> dict[str, Any]:
        """Return learned user preferences."""
        return await asyncio.to_thread(self._get_preferences_sync)

    def _get_preferences_sync(self) -> dict[str, Any]:
        if not self._conn:
            return {}
        rows = self._conn.execute(
            "SELECT key, value, confidence, times_seen FROM preferences ORDER BY confidence DESC"
        ).fetchall()
        return {
            r["key"]: {"value": r["value"], "confidence": round(r["confidence"], 2), "seen": r["times_seen"]}
            for r in rows
        }

    async def get_insights(self) -> dict[str, Any]:
        """Usage analytics and patterns."""
        return await asyncio.to_thread(self._get_insights_sync)

    def _get_insights_sync(self) -> dict[str, Any]:
        if not self._conn:
            return {}

        insights: dict[str, Any] = {}

        # Total stats
        row = self._conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as errors, "
            "AVG(duration_ms) as avg_ms FROM interactions"
        ).fetchone()
        if row and row["total"]:
            insights["totals"] = {
                "interactions": row["total"],
                "errors": row["errors"],
                "error_rate_pct": round((row["errors"] / row["total"]) * 100, 1) if row["total"] else 0,
                "avg_duration_ms": round(row["avg_ms"], 1) if row["avg_ms"] else 0,
            }

        # Top tools
        tools = self._conn.execute(
            "SELECT tool, COUNT(*) as cnt FROM interactions GROUP BY tool ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        insights["top_tools"] = {t["tool"]: t["cnt"] for t in tools}

        # Tool categories use the explicit catalog, not inferred prefixes.
        categories: Counter[str] = Counter()
        for t in tools:
            category = category_for_tool(t["tool"])
            if category:
                categories[category] += t["cnt"]
        if categories:
            insights["focus_areas"] = dict(categories.most_common())

        slow_tools = self._conn.execute(
            "SELECT tool, AVG(duration_ms) as avg_ms FROM interactions "
            "GROUP BY tool HAVING COUNT(*) >= 1 ORDER BY avg_ms DESC LIMIT 10"
        ).fetchall()
        insights["slow_tools"] = {
            row["tool"]: round(row["avg_ms"], 1) for row in slow_tools if row["avg_ms"] is not None
        }

        failure_hotspots = self._conn.execute(
            "SELECT tool, COUNT(*) as cnt FROM interactions WHERE success=0 GROUP BY tool ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        insights["failure_hotspots"] = {row["tool"]: row["cnt"] for row in failure_hotspots}

        # Sessions
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM sessions").fetchone()
        insights["total_sessions"] = row["cnt"] if row else 0

        return insights

    async def suggest_next(self, current_tool: str) -> list[dict[str, Any]]:
        """Suggest next tools based on learned sequences."""
        return await asyncio.to_thread(self._suggest_next_sync, current_tool)

    def _suggest_next_sync(self, current_tool: str) -> list[dict[str, Any]]:
        if not self._conn:
            return []
        rows = self._conn.execute(
            "SELECT next_tool, count FROM tool_sequences WHERE prev_tool=? ORDER BY count DESC LIMIT 5",
            (current_tool,),
        ).fetchall()
        total = sum(r["count"] for r in rows) if rows else 1
        return [
            {"tool": r["next_tool"], "probability": round(r["count"] / total, 2), "times": r["count"]} for r in rows
        ]

    async def get_workflows(self) -> list[dict[str, Any]]:
        """Detect common multi-step workflows from tool sequence data."""
        return await asyncio.to_thread(self._get_workflows_sync)

    def _get_workflows_sync(self) -> list[dict[str, Any]]:
        if not self._conn:
            return []

        # Find frequent 2-step and 3-step chains
        rows = self._conn.execute(
            "SELECT prev_tool, next_tool, count FROM tool_sequences WHERE count >= 3 ORDER BY count DESC LIMIT 20"
        ).fetchall()

        workflows = []
        seen = set()
        for r in rows:
            chain = [r["prev_tool"], r["next_tool"]]
            # Try to extend the chain
            ext = self._conn.execute(
                "SELECT next_tool, count FROM tool_sequences "
                "WHERE prev_tool=? AND count >= 2 ORDER BY count DESC LIMIT 1",
                (r["next_tool"],),
            ).fetchone()
            if ext:
                chain.append(ext["next_tool"])

            key = "->".join(chain)
            if key not in seen:
                seen.add(key)
                workflows.append({"chain": chain, "frequency": r["count"]})

        return workflows[:10]

    async def set_preference(self, key: str, value: str):
        """Manually set a preference."""
        await asyncio.to_thread(self._set_preference_sync, key, value)

    def _set_preference_sync(self, key: str, value: str):
        if not self._conn:
            return
        now = time.time()
        self._conn.execute(
            "INSERT INTO preferences (key, value, confidence, times_seen, first_seen, last_seen) "
            "VALUES (?, ?, 1.0, 100, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=?, confidence=1.0, times_seen=100, last_seen=?",
            (key, value, now, now, value, now),
        )
        self._conn.commit()

    async def clear(self):
        """Reset all memory."""
        await asyncio.to_thread(self._clear_sync)

    def _clear_sync(self):
        if not self._conn:
            return
        self._conn.executescript(
            "DELETE FROM interactions; DELETE FROM preferences; DELETE FROM sessions; DELETE FROM tool_sequences;"
        )
        self._conn.commit()
        self._current_session = None
        self._last_tool = None
        self._last_activity = 0

    # ── internal ──

    def _ensure_session(self, now: float) -> int:
        """Return current session id, creating a new one if needed."""
        if self._current_session and (now - self._last_activity) < _SESSION_GAP:
            return self._current_session

        self._finalize_session()

        assert self._conn is not None
        cur = self._conn.execute(
            "INSERT INTO sessions (started_at, last_activity, tool_count) VALUES (?, ?, 0)",
            (now, now),
        )
        self._current_session = cur.lastrowid
        assert self._current_session is not None
        return int(self._current_session)

    def _finalize_session(self):
        """Write a summary for the ending session."""
        if not self._current_session or not self._conn:
            return

        tools = self._conn.execute(
            "SELECT tool, COUNT(*) as cnt FROM interactions WHERE session_id=? GROUP BY tool ORDER BY cnt DESC LIMIT 5",
            (self._current_session,),
        ).fetchall()

        if tools:
            parts = [f"{t['tool']}({t['cnt']})" for t in tools]
            summary = ", ".join(parts)
            self._conn.execute(
                "UPDATE sessions SET summary=? WHERE id=?",
                (summary, self._current_session),
            )
            self._conn.commit()

        self._current_session = None

    @staticmethod
    def _summarize_args(args: dict[str, Any]) -> str:
        """Create a short summary of tool args for storage."""
        interesting = {}
        for k, v in args.items():
            if v is None or v == "" or v is False or v == 0:
                continue
            if isinstance(v, str) and len(v) > 120:
                v = v[:120] + "..."
            interesting[k] = v
        return json.dumps(interesting, default=str) if interesting else "{}"

    @staticmethod
    def _parse_args_summary(summary: str) -> dict[str, Any] | str:
        try:
            return json.loads(summary)
        except Exception:
            return summary
