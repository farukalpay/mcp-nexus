"""Multi-tenant gateway — manages per-client SSH pools.

OAuth credentials map directly to SSH credentials:
  - client_id    = target server IP (or "127.0.0.1" for localhost/owner)
  - client_secret = SSH password for that server
  - ssh_user defaults to "root" (configurable per-token)

Each authenticated client gets a dedicated SSH pool to their server.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
import time
from dataclasses import dataclass, field

from mcp_nexus.config import Settings
from mcp_nexus.transport.ssh import SSHConnection, SSHPool

logger = logging.getLogger(__name__)


@dataclass
class GatewayToken:
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    created_at: float = field(default_factory=time.time)
    # SSH target info (derived from client_id / client_secret)
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_user: str = "root"
    ssh_password: str = ""

    @property
    def is_expired(self) -> bool:
        return time.time() > self.created_at + self.expires_in

    @property
    def pool_key(self) -> str:
        return f"{self.ssh_user}@{self.ssh_host}:{self.ssh_port}"

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "target": f"{self.ssh_user}@{self.ssh_host}:{self.ssh_port}",
        }


class GatewayManager:
    """Manages per-client SSH pools for the multi-tenant gateway.

    Authentication flow:
        1. Client POSTs to /oauth/token with client_id=IP, client_secret=PASSWORD
        2. Gateway validates by attempting SSH connection
        3. On success, creates/reuses an SSH pool and issues a token
        4. Token is used in MCP session — all tool calls route to that pool
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._pools: dict[str, SSHPool] = {}  # pool_key -> SSHPool
        self._tokens: dict[str, GatewayToken] = {}  # access_token -> GatewayToken
        self._lock = asyncio.Lock()
        # Owner pool (localhost) is always available
        self._owner_pool = SSHPool(settings)
        self._owner_host = settings.ssh_host

    async def authenticate(
        self,
        client_id: str,
        client_secret: str,
        ssh_user: str = "",
        ssh_port: int = 0,
    ) -> GatewayToken | None:
        """Authenticate by validating SSH credentials.

        Args:
            client_id: Target server IP or hostname.
            client_secret: SSH password for that server.
            ssh_user: SSH username (defaults to "root").
            ssh_port: SSH port (defaults to 22).
        """
        ssh_host = client_id.strip()
        ssh_password = client_secret
        ssh_user = ssh_user or "root"
        ssh_port = ssh_port or 22

        # Check if this is the owner (localhost)
        is_owner = self._is_localhost(ssh_host)

        if is_owner:
            # Owner uses the pre-configured pool — validate with server password
            if self._settings.ssh_password and ssh_password != self._settings.ssh_password:
                # If server has a password configured, it must match
                # But if no password is set (key-based or localhost), allow it
                if not self._settings.is_localhost:
                    logger.warning("Owner auth failed — wrong password for %s", ssh_host)
                    return None
        else:
            # Remote server — validate by attempting SSH connection
            valid = await self._validate_ssh(ssh_host, ssh_port, ssh_user, ssh_password)
            if not valid:
                logger.warning("Gateway auth failed — SSH to %s@%s:%d", ssh_user, ssh_host, ssh_port)
                return None

        token = GatewayToken(
            access_token=secrets.token_urlsafe(48),
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
        )
        self._tokens[token.access_token] = token

        # Ensure pool exists for this target
        await self._ensure_pool(token)

        logger.info("Gateway token issued for %s", token.pool_key)
        return token

    def validate_token(self, token_str: str) -> GatewayToken | None:
        """Validate a gateway access token."""
        token = self._tokens.get(token_str)
        if token is None or token.is_expired:
            return None
        return token

    def get_pool_for_token(self, token_str: str) -> SSHPool | None:
        """Get the SSH pool associated with a token."""
        token = self.validate_token(token_str)
        if token is None:
            return None

        if self._is_localhost(token.ssh_host):
            return self._owner_pool

        return self._pools.get(token.pool_key)

    def get_owner_pool(self) -> SSHPool:
        """Get the owner's (localhost) pool — used when no auth token is present."""
        return self._owner_pool

    async def _validate_ssh(self, host: str, port: int, user: str, password: str) -> bool:
        """Validate SSH credentials by attempting a connection."""
        import asyncssh
        try:
            conn = await asyncio.wait_for(
                asyncssh.connect(
                    host=host,
                    port=port,
                    username=user,
                    password=password,
                    known_hosts=None,
                ),
                timeout=10,
            )
            # Quick test
            result = await conn.run("echo ok", check=False)
            conn.close()
            return result.stdout.strip() == "ok"
        except Exception as e:
            logger.debug("SSH validation failed for %s@%s:%d — %s", user, host, port, e)
            return False

    async def _ensure_pool(self, token: GatewayToken):
        """Create or reuse an SSH pool for the given token's target."""
        if self._is_localhost(token.ssh_host):
            return  # owner pool is pre-created

        async with self._lock:
            key = token.pool_key
            if key not in self._pools:
                # Create a Settings-like object for the pool
                pool_settings = Settings()
                pool_settings.ssh_host = token.ssh_host
                pool_settings.ssh_port = token.ssh_port
                pool_settings.ssh_user = token.ssh_user
                pool_settings.ssh_password = token.ssh_password
                pool_settings.ssh_pool_size = min(self._settings.ssh_pool_size, 2)  # limit per-client
                self._pools[key] = SSHPool(pool_settings)
                logger.info("Created SSH pool for %s", key)

    def _is_localhost(self, host: str) -> bool:
        """Check if a host is localhost."""
        if host in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            return True
        if host == self._owner_host:
            return True
        try:
            local_ips = {
                addr[4][0]
                for info in socket.getaddrinfo(socket.gethostname(), None)
                for addr in [info]
            }
            local_ips.add("127.0.0.1")
            return host in local_ips
        except Exception:
            return False

    async def cleanup(self):
        """Close expired pools and remove expired tokens."""
        expired_tokens = [k for k, v in self._tokens.items() if v.is_expired]
        for k in expired_tokens:
            del self._tokens[k]

        # Close pools with no active tokens
        active_keys = {t.pool_key for t in self._tokens.values()}
        async with self._lock:
            for key in list(self._pools.keys()):
                if key not in active_keys:
                    await self._pools[key].close()
                    del self._pools[key]
                    logger.info("Closed idle pool for %s", key)

    async def close_all(self):
        """Shut down all pools."""
        await self._owner_pool.close()
        async with self._lock:
            for pool in self._pools.values():
                await pool.close()
            self._pools.clear()
        self._tokens.clear()

    def stats(self) -> dict:
        return {
            "active_tokens": len(self._tokens),
            "active_pools": len(self._pools) + 1,  # +1 for owner
            "targets": [k for k in self._pools.keys()],
        }
