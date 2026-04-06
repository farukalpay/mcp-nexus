#!/usr/bin/env bash
# MCP Nexus — Quick Install Script
# Usage: curl -sSL https://raw.githubusercontent.com/lightcap-ai/mcp-nexus/main/scripts/install.sh | bash

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  MCP Nexus — Remote Server Management via MCP  ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo

# Check Python version
PYTHON=$(command -v python3 || true)
if [ -z "$PYTHON" ]; then
    echo -e "${RED}Error: Python 3.11+ is required${NC}"
    exit 1
fi

PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo -e "${RED}Error: Python 3.11+ required (found $PY_VERSION)${NC}"
    exit 1
fi
echo -e "${GREEN}✓${NC} Python $PY_VERSION"

# Clone or update
INSTALL_DIR="${MCP_NEXUS_DIR:-$HOME/mcp-nexus}"
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only
else
    echo "Installing to $INSTALL_DIR..."
    git clone https://github.com/lightcap-ai/mcp-nexus.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Virtual environment
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    $PYTHON -m venv .venv
fi

source .venv/bin/activate
echo -e "${GREEN}✓${NC} Virtual environment"

# Dependencies
pip install -q -e ".[dev]" 2>/dev/null
echo -e "${GREEN}✓${NC} Dependencies installed"

# .env setup
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "${BLUE}→${NC} Created .env from template — edit it with your server details"
else
    echo -e "${GREEN}✓${NC} .env exists"
fi

echo
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo
echo "  Quick start:"
echo "    cd $INSTALL_DIR"
echo "    source .venv/bin/activate"
echo "    nano .env                        # Set your server credentials"
echo "    mcp-nexus serve                  # Start MCP server"
echo
echo "  Or with Docker:"
echo "    docker compose up -d"
echo
echo "  Health check:"
echo "    mcp-nexus health"
echo
