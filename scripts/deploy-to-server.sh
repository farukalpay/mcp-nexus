#!/usr/bin/env bash
# Deploy MCP Nexus to a remote server as a systemd service.
# This script is meant to be run from the mcp-nexus project root.
#
# Usage: ./scripts/deploy-to-server.sh <SSH_HOST> [SSH_USER] [SSH_PORT]
#
# Example: ./scripts/deploy-to-server.sh 149.102.155.77 root 22

set -euo pipefail

HOST="${1:?Usage: deploy-to-server.sh <SSH_HOST> [SSH_USER] [SSH_PORT]}"
USER="${2:-root}"
PORT="${3:-22}"
REMOTE_DIR="/root/mcp-nexus"

SSH="ssh -o StrictHostKeyChecking=no -p $PORT $USER@$HOST"
SCP="scp -o StrictHostKeyChecking=no -P $PORT"

echo "━━━ Deploying MCP Nexus to $USER@$HOST:$PORT ━━━"

# 1. Create remote directory
$SSH "mkdir -p $REMOTE_DIR"

# 2. Sync code (excluding .env, .git, __pycache__)
rsync -avz --delete \
    --exclude='.env' \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.venv' \
    --exclude='node_modules' \
    --exclude='.DS_Store' \
    -e "ssh -o StrictHostKeyChecking=no -p $PORT" \
    ./ "$USER@$HOST:$REMOTE_DIR/"

echo "✓ Code synced"

# 3. Setup venv & install
$SSH "cd $REMOTE_DIR && python3 -m venv .venv && .venv/bin/pip install -q -e ."
echo "✓ Dependencies installed"

# 4. Copy .env if it exists locally and not on remote
if [ -f ".env" ]; then
    $SSH "test -f $REMOTE_DIR/.env" 2>/dev/null || $SCP .env "$USER@$HOST:$REMOTE_DIR/.env"
fi

# 5. Create systemd service
$SSH "cat > /etc/systemd/system/mcp-nexus.service << 'UNIT'
[Unit]
Description=MCP Nexus — Remote Server Management via MCP
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$REMOTE_DIR
ExecStart=$REMOTE_DIR/.venv/bin/python -m mcp_nexus serve --host 127.0.0.1 --port 8766
Restart=always
RestartSec=3
StartLimitBurst=10
StartLimitIntervalSec=120
OOMScoreAdjust=-500
LimitNOFILE=65535
StandardOutput=journal
StandardError=journal
EnvironmentFile=$REMOTE_DIR/.env

[Install]
WantedBy=multi-user.target
UNIT"

echo "✓ Systemd service created"

# 6. Add nginx location block (if nginx exists)
$SSH "if command -v nginx &>/dev/null; then
    # Check if the location block already exists
    if ! grep -q 'mcp/nexus' /etc/nginx/sites-enabled/* 2>/dev/null; then
        echo '
    # MCP Nexus
    location /mcp/nexus {
        proxy_pass http://127.0.0.1:8766;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \"upgrade\";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
    }

    location /health/nexus {
        proxy_pass http://127.0.0.1:8766/health;
        proxy_read_timeout 5s;
    }

    location /info/nexus {
        proxy_pass http://127.0.0.1:8766/info;
        proxy_read_timeout 5s;
    }

    location /oauth/token {
        proxy_pass http://127.0.0.1:8766/oauth/token;
        proxy_read_timeout 10s;
    }' >> /tmp/nexus_nginx_snippet.txt
        echo '→ Nginx snippet saved to /tmp/nexus_nginx_snippet.txt'
        echo '  Add it inside the server {} block of your nginx config'
    fi
fi"

# 7. Enable and start
$SSH "systemctl daemon-reload && systemctl enable mcp-nexus && systemctl restart mcp-nexus"
echo "✓ Service started"

# 8. Verify
sleep 2
$SSH "systemctl is-active mcp-nexus && echo '✓ MCP Nexus is running' || echo '✗ Service failed to start'"

echo
echo "━━━ Deployment complete ━━━"
echo "  MCP endpoint: https://your-domain.com/mcp/nexus"
echo "  Health check: https://your-domain.com/health/nexus"
echo "  Logs: ssh $USER@$HOST journalctl -u mcp-nexus -f"
