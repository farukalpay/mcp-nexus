"""Audit logging — tracks all tool calls with timestamps."""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    timestamp: float
    tool: str
    client_id: str
    args: dict
    success: bool
    duration_ms: float
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "ts": self.timestamp,
            "tool": self.tool,
            "client": self.client_id,
            "args": {k: v[:100] if isinstance(v, str) and len(v) > 100 else v for k, v in self.args.items()},
            "ok": self.success,
            "ms": round(self.duration_ms, 1),
            "error": self.error,
        }


class AuditLog:
    """In-memory audit log with optional file persistence."""

    def __init__(self, max_entries: int = 5000, log_file: str | None = None):
        self._entries: deque[AuditEntry] = deque(maxlen=max_entries)
        self._log_file = Path(log_file) if log_file else None
        if self._log_file:
            self._log_file.parent.mkdir(parents=True, exist_ok=True)

    def record(self, entry: AuditEntry):
        self._entries.append(entry)
        if self._log_file:
            try:
                with self._log_file.open("a") as f:
                    f.write(json.dumps(entry.to_dict()) + "\n")
            except Exception as e:
                logger.warning("Failed to write audit log: %s", e)

    def recent(self, count: int = 50, tool: str = "") -> list[dict]:
        entries = list(self._entries)
        if tool:
            entries = [e for e in entries if e.tool == tool]
        return [e.to_dict() for e in entries[-count:]]

    def stats(self) -> dict:
        if not self._entries:
            return {"total": 0}

        total = len(self._entries)
        errors = sum(1 for e in self._entries if not e.success)
        tools = {}
        for e in self._entries:
            tools[e.tool] = tools.get(e.tool, 0) + 1

        avg_ms = sum(e.duration_ms for e in self._entries) / total

        return {
            "total": total,
            "errors": errors,
            "error_rate": round(errors / total * 100, 1),
            "avg_duration_ms": round(avg_ms, 1),
            "top_tools": dict(sorted(tools.items(), key=lambda x: -x[1])[:10]),
        }
