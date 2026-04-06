"""Network tools — ports, DNS, SSL, firewall."""

from __future__ import annotations

import json
import shlex

from mcp.server.fastmcp import FastMCP

from mcp_nexus.server import get_pool


def register(mcp: FastMCP):

    @mcp.tool()
    async def check_port(host: str = "localhost", port: int = 80, timeout_sec: int = 3) -> str:
        """Check if a TCP port is open.

        Args:
            host: Host to check.
            port: Port number.
            timeout_sec: Connection timeout.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"timeout {timeout_sec} bash -c 'echo >/dev/tcp/{host}/{port}' 2>/dev/null && echo OPEN || echo CLOSED"
            result = await conn.run(cmd, timeout=timeout_sec + 2)
            is_open = "OPEN" in result
            return json.dumps({"host": host, "port": port, "open": is_open})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def dns_lookup(domain: str, record_type: str = "A") -> str:
        """Perform a DNS lookup.

        Args:
            domain: Domain name to look up.
            record_type: DNS record type (A, AAAA, MX, CNAME, TXT, NS).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"dig +short {shlex.quote(domain)} {shlex.quote(record_type)}"
            result = await conn.run_full(cmd, timeout=10)
            return json.dumps({
                "domain": domain,
                "type": record_type,
                "records": result.stdout.strip() if result.ok else result.stderr.strip(),
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def ssl_info(domain: str, port: int = 443) -> str:
        """Show SSL certificate information for a domain.

        Args:
            domain: Domain to check.
            port: Port (default 443).
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = (
                f"echo | openssl s_client -servername {shlex.quote(domain)} "
                f"-connect {shlex.quote(domain)}:{port} 2>/dev/null | "
                f"openssl x509 -noout -subject -issuer -dates -fingerprint 2>/dev/null"
            )
            result = await conn.run_full(cmd, timeout=15)
            return json.dumps({
                "domain": domain,
                "port": port,
                "certificate": result.stdout.strip() if result.ok else "Could not retrieve certificate",
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def firewall_rules() -> str:
        """Show current firewall (UFW) rules."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run_full("ufw status verbose 2>/dev/null || iptables -L -n 2>/dev/null | head -40")
            return json.dumps({"rules": result.stdout.strip() if result.ok else "Firewall not available"})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def curl_test(url: str, method: str = "GET", headers: dict | None = None, timeout_sec: int = 10) -> str:
        """Make an HTTP request from the server.

        Args:
            url: URL to request.
            method: HTTP method.
            headers: Optional headers dict.
            timeout_sec: Request timeout.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"curl -s -o /dev/null -w '%{{http_code}}|%{{time_total}}|%{{size_download}}' -X {shlex.quote(method)}"
            if headers:
                for k, v in headers.items():
                    cmd += f" -H {shlex.quote(f'{k}: {v}')}"
            cmd += f" --max-time {timeout_sec} {shlex.quote(url)}"
            result = await conn.run_full(cmd, timeout=timeout_sec + 5)

            body_cmd = f"curl -s -X {shlex.quote(method)} --max-time {timeout_sec} {shlex.quote(url)} | head -c 5000"
            body = await conn.run_full(body_cmd, timeout=timeout_sec + 5)

            parts = result.stdout.strip().split("|") if result.ok else []
            return json.dumps({
                "url": url,
                "method": method,
                "status_code": parts[0] if len(parts) > 0 else "error",
                "time_seconds": parts[1] if len(parts) > 1 else "?",
                "size_bytes": parts[2] if len(parts) > 2 else "?",
                "body_preview": body.stdout[:5000] if body.ok else None,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def listening_ports() -> str:
        """Show all listening TCP/UDP ports."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            result = await conn.run("ss -tlnp", timeout=10)
            return json.dumps({"ports": result.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def port_forward(listen_port: int, target_host: str = "127.0.0.1", target_port: int = 0, protocol: str = "tcp") -> str:
        """Set up port forwarding using socat (runs in background).

        Args:
            listen_port: Port to listen on.
            target_host: Host to forward to.
            target_port: Port to forward to (defaults to same as listen_port).
            protocol: Protocol — tcp or udp.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if target_port == 0:
                target_port = listen_port

            # Check socat is available
            check = await conn.run_full("which socat 2>/dev/null")
            if not check.ok:
                return json.dumps({"error": "socat not installed — run: apt install socat"})

            proto = "TCP4" if protocol == "tcp" else "UDP4"
            cmd = (
                f"nohup socat {proto}-LISTEN:{listen_port},fork,reuseaddr "
                f"{proto}:{target_host}:{target_port} "
                f">/dev/null 2>&1 & echo $!"
            )
            result = await conn.run_full(cmd, timeout=10)
            pid = result.stdout.strip()
            return json.dumps({
                "status": "ok",
                "listen_port": listen_port,
                "target": f"{target_host}:{target_port}",
                "protocol": protocol,
                "pid": pid,
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def list_forwards() -> str:
        """List active port forwards (socat and ssh tunnels)."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = "ps aux | grep -E '(socat|ssh.*-[LR])' | grep -v grep"
            result = await conn.run_full(cmd, timeout=10)
            return json.dumps({
                "forwards": result.stdout.strip() if result.stdout.strip() else "(no active forwards)",
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def remove_forward(listen_port: int) -> str:
        """Remove a port forward by killing the socat process on that port.

        Args:
            listen_port: The listening port of the forward to remove.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"pgrep -f 'socat.*LISTEN:{listen_port}' | xargs -r kill 2>&1 && echo OK || echo NOT_FOUND"
            result = await conn.run_full(cmd, timeout=10)
            return json.dumps({
                "listen_port": listen_port,
                "status": "removed" if "OK" in result.stdout else "not_found",
            })
        finally:
            pool.release(conn)

    @mcp.tool()
    async def iptables_forward(src_port: int, dst_host: str, dst_port: int, action: str = "add") -> str:
        """Manage iptables port forwarding (DNAT).

        Args:
            src_port: Source port (incoming traffic).
            dst_host: Destination host IP.
            dst_port: Destination port.
            action: "add" to create rule, "remove" to delete it.
        """
        pool = get_pool()
        conn = await pool.acquire()
        try:
            flag = "-A" if action == "add" else "-D"
            cmds = [
                f"iptables -t nat {flag} PREROUTING -p tcp --dport {src_port} -j DNAT --to-destination {dst_host}:{dst_port}",
                f"iptables {flag} FORWARD -p tcp -d {dst_host} --dport {dst_port} -j ACCEPT",
            ]
            if action == "add":
                cmds.insert(0, "echo 1 > /proc/sys/net/ipv4/ip_forward")

            full_cmd = " && ".join(cmds) + " 2>&1"
            result = await conn.run_full(full_cmd, timeout=10)
            return json.dumps({
                "action": action,
                "rule": f":{src_port} -> {dst_host}:{dst_port}",
                "success": result.exit_code == 0,
                "output": result.stdout.strip() if result.stdout.strip() else result.stderr.strip(),
            })
        finally:
            pool.release(conn)
