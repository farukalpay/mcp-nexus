"""Git operations — status, diff, log, commit, branch, push/pull."""

from __future__ import annotations

import json
import shlex

from mcp.server.fastmcp import FastMCP

from mcp_nexus.server import get_pool


def _git(sub: str, cwd: str) -> str:
    return f"cd {shlex.quote(cwd)} && git {sub}"


def register(mcp: FastMCP):

    @mcp.tool()
    async def git_status(repo_path: str = ".") -> str:
        """Show git status of a repository.

        Args:
            repo_path: Path to the git repository.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run(_git("status --porcelain -b", repo_path), timeout=15)
            branch = await conn.run_full(_git("branch --show-current", repo_path), timeout=5)
            return json.dumps({
                "repo": repo_path,
                "branch": branch.stdout.strip() if branch.ok else "unknown",
                "status": result.strip(),
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_diff(repo_path: str = ".", staged: bool = False, file_path: str = "") -> str:
        """Show git diff.

        Args:
            repo_path: Path to the git repository.
            staged: If True, show staged changes only.
            file_path: Optional specific file to diff.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = "diff --stat"
            if staged:
                cmd += " --cached"
            if file_path:
                cmd += f" -- {shlex.quote(file_path)}"
            stat = await conn.run(_git(cmd, repo_path), timeout=15)

            detail_cmd = cmd.replace("--stat", "")
            detail = await conn.run(_git(detail_cmd, repo_path), timeout=30)
            return json.dumps({
                "repo": repo_path,
                "staged": staged,
                "stat": stat.strip(),
                "diff": detail.strip()[-30000:],
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_log(repo_path: str = ".", count: int = 20, oneline: bool = True) -> str:
        """Show git log.

        Args:
            repo_path: Path to the git repository.
            count: Number of commits to show.
            oneline: Compact one-line format.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            fmt = "--oneline" if oneline else "--format=%H|%an|%ar|%s"
            result = await conn.run(_git(f"log -{count} {fmt}", repo_path), timeout=15)
            return json.dumps({"repo": repo_path, "log": result.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_commit(repo_path: str, message: str, add_all: bool = False, files: list[str] | None = None) -> str:
        """Create a git commit.

        Args:
            repo_path: Path to the git repository.
            message: Commit message.
            add_all: If True, stage all changes before committing.
            files: Specific files to stage (optional).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if add_all:
                await conn.run(_git("add -A", repo_path), timeout=15)
            elif files:
                for f in files:
                    await conn.run(_git(f"add {shlex.quote(f)}", repo_path), timeout=10)

            escaped_msg = message.replace("'", "'\\''")
            result = await conn.run_full(_git(f"commit -m '{escaped_msg}'", repo_path), timeout=30)
            if result.ok:
                sha = await conn.run(_git("rev-parse --short HEAD", repo_path), timeout=5)
                return json.dumps({"status": "ok", "sha": sha.strip(), "message": message})
            return json.dumps({"error": result.stderr.strip() or result.stdout.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_branch(repo_path: str = ".", action: str = "list", name: str = "") -> str:
        """Manage git branches.

        Args:
            repo_path: Path to the git repository.
            action: One of "list", "create", "switch", "delete".
            name: Branch name (required for create/switch/delete).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if action == "list":
                result = await conn.run(_git("branch -a", repo_path), timeout=10)
            elif action == "create" and name:
                result = await conn.run(_git(f"checkout -b {shlex.quote(name)}", repo_path), timeout=10)
            elif action == "switch" and name:
                result = await conn.run(_git(f"checkout {shlex.quote(name)}", repo_path), timeout=10)
            elif action == "delete" and name:
                result = await conn.run(_git(f"branch -d {shlex.quote(name)}", repo_path), timeout=10)
            else:
                return json.dumps({"error": f"Invalid action '{action}' or missing branch name"})
            return json.dumps({"repo": repo_path, "action": action, "output": result.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_pull(repo_path: str = ".", remote: str = "origin", branch: str = "") -> str:
        """Pull latest changes from remote.

        Args:
            repo_path: Path to the git repository.
            remote: Remote name (default: origin).
            branch: Branch to pull (default: current branch).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"pull {shlex.quote(remote)}"
            if branch:
                cmd += f" {shlex.quote(branch)}"
            result = await conn.run_full(_git(cmd, repo_path), timeout=120)
            return json.dumps({
                "status": "ok" if result.ok else "error",
                "output": result.stdout.strip(),
                "errors": result.stderr.strip() if result.stderr else None,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_push(
        repo_path: str = ".", remote: str = "origin", branch: str = "", set_upstream: bool = False,
    ) -> str:
        """Push commits to remote.

        Args:
            repo_path: Path to the git repository.
            remote: Remote name (default: origin).
            branch: Branch to push (default: current branch).
            set_upstream: If True, set upstream tracking.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"push {shlex.quote(remote)}"
            if set_upstream:
                cmd = f"push -u {shlex.quote(remote)}"
            if branch:
                cmd += f" {shlex.quote(branch)}"
            result = await conn.run_full(_git(cmd, repo_path), timeout=120)
            return json.dumps({
                "status": "ok" if result.ok else "error",
                "output": (result.stdout + result.stderr).strip(),
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_stash(repo_path: str = ".", action: str = "push", message: str = "") -> str:
        """Manage git stash.

        Args:
            repo_path: Path to the git repository.
            action: One of "push", "pop", "list", "drop".
            message: Stash message (for push action).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if action == "push":
                cmd = "stash push"
                if message:
                    cmd += f" -m {shlex.quote(message)}"
            elif action == "pop":
                cmd = "stash pop"
            elif action == "list":
                cmd = "stash list"
            elif action == "drop":
                cmd = "stash drop"
            else:
                return json.dumps({"error": f"Invalid stash action: {action}"})
            result = await conn.run_full(_git(cmd, repo_path), timeout=15)
            return json.dumps({"action": action, "output": result.stdout.strip(), "ok": result.ok})
        finally:
            pool.release(conn)
