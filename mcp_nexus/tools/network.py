"""Network tools for connectivity, forwarding, and inspection."""

from __future__ import annotations

import json
import shlex

from mcp.server.fastmcp import FastMCP

from mcp_nexus.server import get_pool


def register(mcp: FastMCP):

    @mcp.tool()
    async def check_port(host: str = "localhost", port: int = 80, timeout_sec: int = 3) -> str:
        """Check if a TCP port is open."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            capabilities = await conn.probe_capabilities()
            if capabilities.has("nc"):
                cmd = f"nc -z -w {timeout_sec} {shlex.quote(host)} {port} >/dev/null 2>&1 && echo OPEN || echo CLOSED"
            else:
                cmd = (
                    f"bash -lc 'timeout {timeout_sec} bash -c "
                    f'"echo >/dev/tcp/{host}/{port}" 2>/dev/null && echo OPEN || echo CLOSED\''
                )
            result = await conn.run_full(cmd, timeout=timeout_sec + 5)
            return json.dumps({"host": host, "port": port, "open": "OPEN" in result.stdout})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def dns_lookup(domain: str, record_type: str = "A") -> str:
        """Perform a DNS lookup."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            capabilities = await conn.probe_capabilities()
            if capabilities.has("dig"):
                cmd = f"dig +short {shlex.quote(domain)} {shlex.quote(record_type)}"
            else:
                cmd = f"getent ahosts {shlex.quote(domain)}"
            result = await conn.run_full(cmd, timeout=10)
            return json.dumps(
                {
                    "domain": domain,
                    "type": record_type,
                    "records": result.stdout.strip() if result.ok else result.stderr.strip(),
                }
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def ssl_info(domain: str, port: int = 443) -> str:
        """Show SSL certificate information for a domain."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = (
                f"echo | openssl s_client -servername {shlex.quote(domain)} "
                f"-connect {shlex.quote(domain)}:{port} 2>/dev/null | "
                "openssl x509 -noout -subject -issuer -dates -fingerprint 2>/dev/null"
            )
            result = await conn.run_full(cmd, timeout=15)
            return json.dumps(
                {
                    "domain": domain,
                    "port": port,
                    "certificate": result.stdout.strip() if result.ok else "Could not retrieve certificate",
                }
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def firewall_rules() -> str:
        """Show current firewall rules."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            capabilities = await conn.probe_capabilities()
            if capabilities.has("ufw"):
                cmd = "ufw status verbose 2>/dev/null"
            elif capabilities.has("iptables"):
                cmd = "iptables -L -n 2>/dev/null | head -80"
            else:
                return json.dumps({"error": "No supported firewall tool detected"})
            result = await conn.run_full(cmd, timeout=15)
            return json.dumps({"rules": result.stdout.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def curl_test(url: str, method: str = "GET", headers: dict | None = None, timeout_sec: int = 10) -> str:
        """Make an HTTP request from the server."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = (
                f"curl -s -o /dev/null -w '%{{http_code}}|%{{time_total}}|%{{size_download}}' -X {shlex.quote(method)}"
            )
            if headers:
                for key, value in headers.items():
                    cmd += f" -H {shlex.quote(f'{key}: {value}')}"
            cmd += f" --max-time {timeout_sec} {shlex.quote(url)}"
            result = await conn.run_full(cmd, timeout=timeout_sec + 5)
            body_cmd = f"curl -s -X {shlex.quote(method)} --max-time {timeout_sec} {shlex.quote(url)} | head -c 5000"
            body = await conn.run_full(body_cmd, timeout=timeout_sec + 5)
            parts = result.stdout.strip().split("|") if result.ok else []
            return json.dumps(
                {
                    "url": url,
                    "method": method,
                    "status_code": parts[0] if len(parts) > 0 else "error",
                    "time_seconds": parts[1] if len(parts) > 1 else "?",
                    "size_bytes": parts[2] if len(parts) > 2 else "?",
                    "body_preview": body.stdout[:5000] if body.ok else None,
                }
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def listening_ports() -> str:
        """Show all listening TCP/UDP ports."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            capabilities = await conn.probe_capabilities()
            if capabilities.has("ss"):
                cmd = "ss -tulnp"
            elif capabilities.has("netstat"):
                cmd = "netstat -tulnp"
            elif capabilities.has("lsof"):
                cmd = "lsof -i -P -n | grep LISTEN"
            else:
                return json.dumps({"error": "No supported socket inspection command detected"})
            result = await conn.run_full(cmd, timeout=10)
            return json.dumps({"ports": result.stdout.strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def port_forward(
        listen_port: int, target_host: str = "127.0.0.1", target_port: int = 0, protocol: str = "tcp"
    ) -> str:
        """Set up port forwarding using socat (runs in background)."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if target_port == 0:
                target_port = listen_port
            capabilities = await conn.probe_capabilities()
            if not capabilities.has("socat"):
                return json.dumps({"error": "socat not installed"})
            proto = "TCP4" if protocol == "tcp" else "UDP4"
            cmd = (
                f"nohup socat {proto}-LISTEN:{listen_port},fork,reuseaddr "
                f"{proto}:{target_host}:{target_port} >/dev/null 2>&1 & echo $!"
            )
            result = await conn.run_full(cmd, timeout=10)
            return json.dumps(
                {
                    "status": "ok",
                    "listen_port": listen_port,
                    "target": f"{target_host}:{target_port}",
                    "protocol": protocol,
                    "pid": result.stdout.strip(),
                }
            )
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
            return json.dumps({"forwards": result.stdout.strip() if result.stdout.strip() else "(no active forwards)"})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def remove_forward(listen_port: int) -> str:
        """Remove a port forward by killing the socat process on that port."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = f"pgrep -f 'socat.*LISTEN:{listen_port}' | xargs -r kill 2>&1 && echo OK || echo NOT_FOUND"
            result = await conn.run_full(cmd, timeout=10)
            return json.dumps(
                {"listen_port": listen_port, "status": "removed" if "OK" in result.stdout else "not_found"}
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def iptables_forward(src_port: int, dst_host: str, dst_port: int, action: str = "add") -> str:
        """Manage iptables port forwarding (DNAT)."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            capabilities = await conn.probe_capabilities()
            if not capabilities.has("iptables"):
                return json.dumps({"error": "iptables is not available on this host"})
            flag = "-A" if action == "add" else "-D"
            cmds = [
                "iptables -t nat "
                f"{flag} PREROUTING -p tcp --dport {src_port} "
                f"-j DNAT --to-destination {dst_host}:{dst_port}",
                f"iptables {flag} FORWARD -p tcp -d {dst_host} --dport {dst_port} -j ACCEPT",
            ]
            if action == "add":
                cmds.insert(0, "echo 1 > /proc/sys/net/ipv4/ip_forward")
            result = await conn.run_full(" && ".join(cmds) + " 2>&1", timeout=10)
            return json.dumps(
                {
                    "action": action,
                    "rule": f":{src_port} -> {dst_host}:{dst_port}",
                    "success": result.exit_code == 0,
                    "output": result.stdout.strip() if result.stdout.strip() else result.stderr.strip(),
                }
            )
        finally:
            pool.release(conn)

    @mcp.tool()
    async def port_scan(host: str, ports: list[int], timeout_sec: int = 2) -> str:
        """Scan a bounded list of TCP ports from the target host."""
        if not ports:
            return json.dumps({"error": "ports is required"})
        pool = get_pool()
        conn = await pool.acquire()
        try:
            capabilities = await conn.probe_capabilities()
            results = []
            for port in ports[:100]:
                if capabilities.has("nc"):
                    cmd = (
                        f"nc -z -w {timeout_sec} {shlex.quote(host)} {port} >/dev/null 2>&1 && echo open || echo closed"
                    )
                else:
                    cmd = (
                        f"bash -lc 'timeout {timeout_sec} bash -c "
                        f'"echo >/dev/tcp/{host}/{port}" >/dev/null 2>&1 && echo open || echo closed\''
                    )
                result = await conn.run_full(cmd, timeout=timeout_sec + 4)
                results.append({"port": port, "status": result.stdout.strip() or "closed"})
            return json.dumps({"host": host, "ports": results})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def network_route(target: str = "") -> str:
        """Inspect routing table or the route to a specific target."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            if target:
                cmd = (
                    f"ip route get {shlex.quote(target)} 2>/dev/null || route -n get {shlex.quote(target)} 2>/dev/null"
                )
            else:
                cmd = "ip route show 2>/dev/null || netstat -rn 2>/dev/null"
            result = await conn.run_full(cmd, timeout=10)
            return json.dumps({"target": target or None, "routes": (result.stdout + result.stderr).strip()})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def trace_route(host: str, max_hops: int = 20) -> str:
        """Run traceroute or tracepath to a host."""
        pool = get_pool()
        conn = await pool.acquire()
        try:
            traceroute = await conn.run_full("which traceroute 2>/dev/null")
            if traceroute.ok:
                cmd = f"traceroute -m {max_hops} {shlex.quote(host)}"
            else:
                tracepath = await conn.run_full("which tracepath 2>/dev/null")
                if tracepath.ok:
                    cmd = f"tracepath -m {max_hops} {shlex.quote(host)}"
                else:
                    return json.dumps({"error": "Neither traceroute nor tracepath is installed"})
            result = await conn.run_full(cmd, timeout=120)
            return json.dumps({"host": host, "output": (result.stdout + result.stderr).strip()[-20000:]})
        finally:
            pool.release(conn)

    @mcp.tool()
    async def ssh_tunnel(
        mode: str = "local",
        bind_port: int = 0,
        target_host: str = "127.0.0.1",
        target_port: int = 0,
        gateway_host: str = "",
        gateway_user: str = "",
        gateway_port: int = 22,
    ) -> str:
        """Start a local or reverse SSH tunnel from the target host."""
        if bind_port <= 0 or target_port <= 0 or not gateway_host or not gateway_user:
            return json.dumps({"error": "bind_port, target_port, gateway_host, and gateway_user are required"})
        if mode not in {"local", "reverse"}:
            return json.dumps({"error": "mode must be one of: local, reverse"})

        flag = "-L" if mode == "local" else "-R"
        tunnel_spec = f"{bind_port}:{target_host}:{target_port}"
        pool = get_pool()
        conn = await pool.acquire()
        try:
            cmd = (
                "nohup ssh -o StrictHostKeyChecking=no -o ExitOnForwardFailure=yes "
                f"-N {flag} {shlex.quote(tunnel_spec)} -p {gateway_port} "
                f"{shlex.quote(gateway_user)}@{shlex.quote(gateway_host)} >/dev/null 2>&1 & echo $!"
            )
            result = await conn.run_full(cmd, timeout=20)
            return json.dumps(
                {
                    "mode": mode,
                    "bind_port": bind_port,
                    "target": f"{target_host}:{target_port}",
                    "gateway": f"{gateway_user}@{gateway_host}:{gateway_port}",
                    "pid": result.stdout.strip(),
                }
            )
        finally:
            pool.release(conn)
