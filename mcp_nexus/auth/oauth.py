"""OAuth2 authentication — gateway mode.

In gateway mode, OAuth credentials map to SSH credentials:
  - client_id    = target server IP/hostname
  - client_secret = SSH password
  - ssh_user     = SSH username (default: root)
  - ssh_port     = SSH port (default: 22)

The gateway validates credentials by attempting an SSH connection.
On success, a token is issued that routes all MCP tool calls to that server.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# OAuth is now handled by GatewayManager in gateway.py
# This module is kept for backwards compatibility and documentation.
# See gateway.py for the full implementation.
