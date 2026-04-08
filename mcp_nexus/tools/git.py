"""Git operations for repository inspection and delivery flows."""

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
        """Show git status of a repository."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run(_git("status --porcelain -b", repo_path), timeout=15)
            branch = await conn.run_full(_git("branch --show-current", repo_path), timeout=5)
            return json.dumps(
                {
                    "repo": repo_path,
                    "branch": branch.stdout.strip() if branch.ok else "unknown",
                    "status": result.strip(),
                }
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_diff(repo_path: str = ".", staged: bool = False, file_path: str = "") -> str:
        """Show git diff."""
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
            return json.dumps(
                {
                    "repo": repo_path,
                    "staged": staged,
                    "stat": stat.strip(),
                    "diff": detail.strip()[-30000:],
                }
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_log(repo_path: str = ".", count: int = 20, oneline: bool = True) -> str:
        """Show git log."""
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
        """Create a git commit."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if add_all:
                await conn.run(_git("add -A", repo_path), timeout=15)
            elif files:
                for file_path in files:
                    await conn.run(_git(f"add {shlex.quote(file_path)}", repo_path), timeout=10)

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
        """Manage git branches."""
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
        """Pull latest changes from remote."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"pull {shlex.quote(remote)}"
            if branch:
                cmd += f" {shlex.quote(branch)}"
            result = await conn.run_full(_git(cmd, repo_path), timeout=120)
            return json.dumps(
                {
                    "status": "ok" if result.ok else "error",
                    "output": result.stdout.strip(),
                    "errors": result.stderr.strip() if result.stderr else None,
                }
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_push(
        repo_path: str = ".",
        remote: str = "origin",
        branch: str = "",
        set_upstream: bool = False,
    ) -> str:
        """Push commits to remote."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"push {shlex.quote(remote)}"
            if set_upstream:
                cmd = f"push -u {shlex.quote(remote)}"
            if branch:
                cmd += f" {shlex.quote(branch)}"
            result = await conn.run_full(_git(cmd, repo_path), timeout=120)
            return json.dumps(
                {"status": "ok" if result.ok else "error", "output": (result.stdout + result.stderr).strip()}
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_stash(repo_path: str = ".", action: str = "push", message: str = "") -> str:
        """Manage git stash."""
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

    @mcp.tool()
    async def git_stage(repo_path: str = ".", files: list[str] | None = None, add_all: bool = False) -> str:
        """Stage files explicitly or stage the entire worktree when requested."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if add_all:
                result = await conn.run_full(_git("add -A", repo_path), timeout=20)
                staged = ["<all>"]
            elif files:
                staged = files
                for file_path in files:
                    result = await conn.run_full(_git(f"add {shlex.quote(file_path)}", repo_path), timeout=20)
                    if not result.ok:
                        return json.dumps({"error": result.stderr.strip() or result.stdout.strip(), "file": file_path})
            else:
                return json.dumps({"error": "Either files or add_all must be provided"})
            return json.dumps({"repo": repo_path, "staged": staged, "ok": result.ok})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_show(repo_path: str = ".", ref: str = "HEAD", file_path: str = "", stat: bool = True) -> str:
        """Show a commit, object, or file revision."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            target = f"{ref}:{file_path}" if file_path else ref
            flags = "--stat " if stat and not file_path else ""
            result = await conn.run_full(_git(f"show {flags}{shlex.quote(target)}", repo_path), timeout=30)
            return json.dumps(
                {"repo": repo_path, "ref": ref, "target": target, "output": result.stdout.strip()[-40000:]}
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_fetch(repo_path: str = ".", remote: str = "origin", prune: bool = False, tags: bool = False) -> str:
        """Fetch remote refs without changing the working tree."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            flags = []
            if prune:
                flags.append("--prune")
            if tags:
                flags.append("--tags")
            result = await conn.run_full(
                _git(f"fetch {' '.join(flags)} {shlex.quote(remote)}".strip(), repo_path),
                timeout=120,
            )
            return json.dumps(
                {
                    "repo": repo_path,
                    "remote": remote,
                    "ok": result.ok,
                    "output": (result.stdout + result.stderr).strip(),
                }
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_remotes(repo_path: str = ".") -> str:
        """Inspect configured git remotes and tracking branches."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            remotes = await conn.run_full(_git("remote -v", repo_path), timeout=15)
            branches = await conn.run_full(_git("branch -vv", repo_path), timeout=15)
            return json.dumps(
                {"repo": repo_path, "remotes": remotes.stdout.strip(), "branches": branches.stdout.strip()}
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_blame(repo_path: str, file_path: str, line_number: int = 0) -> str:
        """Blame a file or a specific line for authorship context."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if line_number > 0:
                cmd = _git(f"blame -L {line_number},{line_number} -- {shlex.quote(file_path)}", repo_path)
            else:
                cmd = _git(f"blame -- {shlex.quote(file_path)}", repo_path)
            result = await conn.run_full(cmd, timeout=30)
            return json.dumps(
                {
                    "repo": repo_path,
                    "file": file_path,
                    "line": line_number or None,
                    "output": result.stdout.strip()[-20000:],
                }
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def git_tags(repo_path: str = ".", pattern: str = "") -> str:
        """List git tags with optional pattern filtering."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = "tag --list"
            if pattern:
                cmd += f" {shlex.quote(pattern)}"
            result = await conn.run_full(_git(cmd, repo_path), timeout=15)
            return json.dumps({"repo": repo_path, "pattern": pattern or None, "tags": result.stdout.strip()})
        finally:
            pool.release(conn)
