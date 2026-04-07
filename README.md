<p align="center">
  <br/>
  <img src="https://img.shields.io/badge/MCP-Nexus-6366f1?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjIiPjxwYXRoIGQ9Ik0xMiAyTDIgN2wxMCA1IDEwLTV6Ii8+PHBhdGggZD0iTTIgMTdsMTAgNSAxMC01Ii8+PHBhdGggZD0iTTIgMTJsMTAgNSAxMC01Ii8+PC9zdmc+" alt="MCP Nexus"/>
  <br/>
  <h1 align="center">MCP Nexus</h1>
  <p align="center">
    <strong>Turn any AI assistant into a full-stack DevOps engineer for your server.</strong>
    <br/>
    70+ tools for files, terminal, git, services, databases, debugging, monitoring, and deployment<br/>
    — with a built-in intelligence layer that learns how you work.
  </p>
  <p align="center">
    <a href="#hosted-gateway">Hosted Gateway</a> &bull;
    <a href="#quick-start">Quick Start</a> &bull;
    <a href="#intelligence">Intelligence</a> &bull;
    <a href="#tools">70+ Tools</a> &bull;
    <a href="#architecture">Architecture</a> &bull;
    <a href="#configuration">Config</a>
  </p>
  <p align="center">
    <img src="https://img.shields.io/github/license/lightcap-ai/mcp-nexus?style=flat-square" alt="License"/>
  </p>
</p>

---

## Hosted Gateway

**Don't want to install anything?** Use the Lightcap-hosted MCP Nexus instance as a gateway to your servers:

```
https://lightcap.ai/mcp/nexus
```

Connect your AI client directly — authenticate once, provide your server's SSH credentials, and manage any server through our gateway. No installation required on your end.

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nexus": {
      "url": "https://lightcap.ai/mcp/nexus",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

**Claude Code** (`.mcp.json`):

```json
{
  "mcpServers": {
    "nexus": {
      "url": "https://lightcap.ai/mcp/nexus"
    }
  }
}
```

### Get your token

Your server IP is your client ID. Your SSH password is your secret.

```bash
curl -X POST https://lightcap.ai/mcp/nexus/oauth/token \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "client_credentials",
    "client_id": "YOUR_SERVER_IP",
    "client_secret": "YOUR_SSH_PASSWORD"
  }'
```

The gateway validates by connecting to your server via SSH. Once authenticated, all 87 MCP tools operate on YOUR server through the Lightcap gateway. Full audit logging, rate limiting, and connection pooling included.

```
Your AI ──MCP/HTTPS──> lightcap.ai/mcp/nexus ──SSH──> Your Server
                            (gateway)                   (target)
```

> **Self-hosted?** Skip the gateway and run MCP Nexus on your own machine — see [Quick Start](#quick-start) below.

---

## Why MCP Nexus?

Most MCP servers give you tools. Nexus gives you tools **that remember**.

Every command you run, every service you restart, every file you edit — Nexus quietly learns your patterns. The next time you connect, it already knows what you were working on, which paths you use most, and what you'll probably need next.

```
You (via Claude/GPT) ──MCP──> MCP Nexus ──SSH──> Your Server
                                  │                   ├── Files (read, write, edit, search, diff)
                                  │                   ├── Terminal (execute commands, scripts)
                                  │                   ├── Git (commit, push, pull, branch)
                                  │                   ├── Services (systemd, docker, nginx)
                                  │                   ├── Database (PostgreSQL queries)
                                  │                   ├── Debug (lint, typecheck, syntax, TODOs)
                                  │                   ├── Monitoring (health, metrics, logs)
                                  │                   ├── Deploy (sync, restart, rollback)
                                  │                   ├── Network (ports, DNS, SSL, forwarding)
                                  │                   └── Packages (pip, apt, npm)
                                  │
                              Intelligence
                            ├── Session memory
                            ├── Preference learning
                            ├── Workflow detection
                            └── Smart suggestions
```

### What makes it different

| | Other MCP servers | MCP Nexus |
|---|---|---|
| **Hosted gateway** | Install-only | Use `lightcap.ai/mcp/nexus` instantly — zero setup |
| **Memory** | Stateless — every session starts from zero | Remembers your sessions, preferences, and workflows |
| **Scope** | Single-purpose (just files, just git) | 70+ tools covering the full DevOps stack |
| **Debugging** | None | Lint, typecheck, syntax check, error search, symbol finder |
| **Connection** | Spawns new processes per call | Pooled SSH connections with auto-reconnect |
| **Recovery** | You notice when things break | Watchdog auto-restarts crashed services |
| **Auth** | None or basic | OAuth2 with scoped tokens and rate limiting |
| **Audit** | None | Every tool call logged with timing |

---

## Quick Start

### Install

```bash
pip install mcp-nexus
```

Or from source:

```bash
git clone https://github.com/lightcap-ai/mcp-nexus.git
cd mcp-nexus
pip install -e .
```

### Configure

```bash
cp .env.example .env
# Edit with your server credentials
```

Minimal `.env`:

```env
NEXUS_SSH_HOST=your-server-ip
NEXUS_SSH_KEY_PATH=~/.ssh/id_rsa
```

> **Note:** `NEXUS_SSH_USER` defaults to `root`. If your server uses a different user, set it explicitly. Most cloud servers (AWS, DigitalOcean, Contabo, Hetzner) default to root access, so you typically don't need to change this. If you use a non-root user, make sure it has sudo privileges for service management tools.

### Connect to your AI

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nexus": {
      "command": "mcp-nexus",
      "args": ["serve", "--transport", "stdio"]
    }
  }
}
```

**Claude Code** (`.mcp.json`):

```json
{
  "mcpServers": {
    "nexus": {
      "command": "mcp-nexus",
      "args": ["serve", "--transport", "stdio"]
    }
  }
}
```

**Remote / HTTP mode** (for web apps, shared teams):

```bash
mcp-nexus serve                # starts on port 8766
# Then connect via: http://your-host:8766/mcp
```

**Docker:**

```bash
docker compose up -d
```

---

## Intelligence

The intelligence layer is what sets Nexus apart. It runs a local SQLite database (`~/.mcp-nexus/memory.db`) that silently tracks every interaction and builds up knowledge about how you work.

### What it learns

- **Session context** — "You were last working on nginx config, editing `/etc/nginx/sites-enabled/api.conf`. Your last session was 2 hours ago with 14 tool calls."
- **Preferences** — Detects your default repo path, preferred working directory, watched services, and more. Auto-detected from repeated usage, no configuration needed.
- **Workflow patterns** — Recognizes sequences like `git_status -> git_diff -> git_commit` and surfaces them as detected workflows.
- **Smart suggestions** — After running `git_commit`, suggests `git_push` based on your history.

### Intelligence tools

| Tool | What it does |
|------|-------------|
| `nexus_recall` | Pick up where you left off — shows recent actions, session context, errors, and learned preferences |
| `nexus_insights` | Usage analytics — top tools, focus areas, error rates, detected workflows |
| `nexus_suggest` | Context-aware suggestions for what to do next |
| `nexus_preferences` | View, set, or clear learned preferences |

### Example

```
You: "What was I working on last time?"

Claude calls nexus_recall ->
{
  "last_session": {
    "ended_ago_min": 127.3,
    "tools_used": 14
  },
  "recent_actions": [
    {"tool": "edit_file", "args": {"path": "/etc/nginx/sites-enabled/api.conf"}, "ok": true},
    {"tool": "restart_service", "args": {"service_name": "nginx"}, "ok": true},
    {"tool": "curl_test", "args": {"url": "https://api.example.com/health"}, "ok": true}
  ],
  "preferences": {
    "working_directory": {"value": "/etc/nginx", "confidence": 0.8, "seen": 6},
    "watched_service": {"value": "nginx", "confidence": 0.7, "seen": 4}
  }
}
```

Disable intelligence with `NEXUS_INTELLIGENCE=false` if you prefer stateless mode.

---

## Tools

### Filesystem (19 tools)

| Tool | Description |
|------|-------------|
| `read_file` | Read file with line range support (offset/limit) |
| `write_file` | Write/create file (auto-creates directories) |
| `edit_file` | Search-and-replace editing (single or all occurrences) |
| `list_directory` | List directory contents with hidden/long format |
| `search_files` | Find files by glob pattern |
| `search_content` | Grep/ripgrep content search with regex |
| `file_info` | File metadata (size, permissions, dates, owner) |
| `move_file` | Move or rename files |
| `delete_file` | Delete with safety guards on protected paths |
| `create_directory` | Create directories recursively |
| `tree` | Directory tree visualization |
| `tail_file` | Read last N lines (with live follow mode for logs) |
| `head_file` | Read first N lines |
| `chmod_file` | Change file permissions |
| `chown_file` | Change file ownership |
| `file_exists` | Check if file/directory/symlink exists |
| `batch_read` | Read multiple files at once |
| `replace_in_file` | Regex find-and-replace via sed |
| `count_lines` | Count lines, words, and characters |

### Terminal (4 tools)

| Tool | Description |
|------|-------------|
| `execute_command` | Run any shell command (up to 600s timeout) |
| `execute_script` | Run multi-line scripts (bash, python, etc.) |
| `environment_info` | OS, kernel, Python, Node versions |
| `which_command` | Check if a command exists |

### Git (8 tools)

| Tool | Description |
|------|-------------|
| `git_status` | Working tree status |
| `git_diff` | Staged/unstaged diffs |
| `git_log` | Commit history |
| `git_commit` | Create commits |
| `git_branch` | List, create, switch, delete branches |
| `git_pull` | Pull from remote |
| `git_push` | Push to remote |
| `git_stash` | Stash management |

### Debug & Code Analysis (8 tools)

| Tool | Description |
|------|-------------|
| `lint_python` | Run ruff/flake8 linter (with auto-fix) |
| `typecheck` | Run mypy/pyright type checker |
| `syntax_check` | Validate syntax (Python, JS, JSON, YAML, Bash, XML) |
| `find_todos` | Find TODO/FIXME/HACK/BUG comments |
| `code_symbols` | Find function/class/import definitions |
| `compare_files` | Diff two files |
| `find_errors` | Search logs/code for error patterns |
| `python_trace` | Analyze Python for missing imports and undefined names |

### Process & Services (10 tools)

| Tool | Description |
|------|-------------|
| `list_services` | List systemd services |
| `service_status` | Detailed service status |
| `restart_service` / `start_service` / `stop_service` | Service lifecycle |
| `view_logs` | Journalctl log viewer with time filtering |
| `list_processes` | Running processes (sort by CPU/mem) |
| `kill_process` | Send signals to processes |
| `cron_list` / `cron_add` | Crontab management |

### Database (5 tools)

| Tool | Description |
|------|-------------|
| `db_query` | Execute SQL queries (auto-limited) |
| `db_tables` | List tables with sizes |
| `db_schema` | Table columns and types |
| `db_execute` | INSERT/UPDATE/DELETE/CREATE |
| `db_size` | Database size and connections |

### Monitoring (8 tools)

| Tool | Description |
|------|-------------|
| `server_health` | Comprehensive health check |
| `disk_usage` | Disk space |
| `memory_usage` | RAM + top memory consumers |
| `cpu_usage` | Load average + top CPU consumers |
| `network_stats` | Interfaces and connection stats |
| `active_connections` | Listening ports and connections |
| `nginx_status` | Nginx status, config test, errors |
| `docker_status` | Container status and resource usage |

### Deployment (6 tools)

| Tool | Description |
|------|-------------|
| `deploy_sync` | Rsync files to server |
| `deploy_service` | Restart with pre/post commands |
| `create_backup` | Timestamped tar.gz backups |
| `list_backups` | Available backups |
| `restore_backup` | Restore from backup |
| `pip_install` | Install Python packages |

### Network & Port Forwarding (10 tools)

| Tool | Description |
|------|-------------|
| `check_port` | TCP port check |
| `dns_lookup` | DNS records (A, MX, CNAME, etc.) |
| `ssl_info` | SSL certificate details |
| `firewall_rules` | UFW/iptables rules |
| `curl_test` | HTTP requests from server |
| `listening_ports` | All listening ports |
| `port_forward` | Set up port forwarding via socat |
| `list_forwards` | List active port forwards and SSH tunnels |
| `remove_forward` | Remove a port forward |
| `iptables_forward` | Manage iptables DNAT port forwarding |

### Package Management (5 tools)

| Tool | Description |
|------|-------------|
| `pip_list` | List installed Python packages |
| `pip_show` | Package details and dependencies |
| `apt_list` | List system packages (Debian/Ubuntu) |
| `apt_install` | Install system packages (with dry-run mode) |
| `npm_list` | List npm packages (local or global) |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       MCP Nexus Server                       │
│                                                              │
│  ┌───────────┐  ┌───────────┐  ┌──────────┐  ┌───────────┐ │
│  │Intelligence│  │   Rate    │  │  Audit   │  │ Watchdog  │ │
│  │  Engine    │  │  Limiter  │  │   Log    │  │   (bg)    │ │
│  │ (SQLite)   │  │(per-client)│  │(tracking)│  │(auto-heal)│ │
│  └─────┬─────┘  └─────┬─────┘  └────┬─────┘  └─────┬─────┘ │
│        │               │              │               │      │
│  ┌─────▼───────────────▼──────────────▼───────────────▼────┐ │
│  │                    MCP Tool Layer (70+ tools)            │ │
│  │  filesystem │ terminal │ git │ process │ database       │ │
│  │  monitor │ deploy │ network │ debug │ packages          │ │
│  │  intelligence                                           │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                              │                               │
│  ┌──────────────────────────▼──────────────────────────────┐ │
│  │                SSH Connection Pool                       │ │
│  │  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐                      │ │
│  │  │conn1│ │conn2│ │conn3│ │conn4│  auto-reconnect       │ │
│  │  └─────┘ └─────┘ └─────┘ └─────┘                      │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                              │                               │
│               ┌──────────────┴──────────────┐                │
│               │ localhost? -> direct execute │                │
│               │ remote?    -> SSH connection │                │
│               └──────────────┬──────────────┘                │
└──────────────────────────────┼───────────────────────────────┘
                               │
                               ▼
                   ┌───────────────────────┐
                   │    Target Server      │
                   │  (your infrastructure)│
                   └───────────────────────┘
```

### How it works

1. **AI sends MCP tool call** (e.g., `read_file("/etc/nginx/nginx.conf")`)
2. **Nexus acquires a pooled SSH connection** (or runs locally if target is localhost)
3. **Executes the command** on the remote server via SSH
4. **Records the interaction** in the intelligence database + audit log
5. **Returns structured JSON** to the AI

### Localhost mode

When `NEXUS_SSH_HOST` is `127.0.0.1` or `localhost`, Nexus skips SSH entirely and executes commands directly. This is auto-detected — no configuration needed. Great for local development and CI pipelines.

---

## Configuration

All configuration via environment variables (loaded from `.env`):

### SSH

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_SSH_HOST` | `127.0.0.1` | Target server |
| `NEXUS_SSH_PORT` | `22` | SSH port |
| `NEXUS_SSH_USER` | `root` | SSH username (defaults to root — most cloud servers use root) |
| `NEXUS_SSH_PASSWORD` | | SSH password |
| `NEXUS_SSH_KEY_PATH` | | SSH private key path (recommended over password) |
| `NEXUS_SSH_POOL_SIZE` | `4` | Max concurrent SSH connections |

> **About the SSH user:** The default is `root` because most cloud servers (AWS, DigitalOcean, Contabo, Hetzner, Vultr) grant root access by default. If you use a non-root user like `ubuntu` or `deploy`, set `NEXUS_SSH_USER=ubuntu` in your `.env`. Non-root users need sudo privileges for service management, package installation, and system-level operations.

### Server

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_HOST` | `0.0.0.0` | Bind host |
| `NEXUS_PORT` | `8766` | Bind port |
| `NEXUS_MCP_PATH` | `/mcp` | MCP endpoint path |
| `NEXUS_LOG_LEVEL` | `info` | Logging level |

### Intelligence

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_INTELLIGENCE` | `true` | Enable/disable the intelligence layer |
| `NEXUS_DATA_DIR` | `~/.mcp-nexus` | Where memory database is stored |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_OAUTH_CLIENT_ID` | `nexus-default` | OAuth2 client ID |
| `NEXUS_OAUTH_CLIENT_SECRET` | | OAuth2 client secret |
| `NEXUS_RATE_LIMIT_RPM` | `120` | Requests per minute |
| `NEXUS_RATE_LIMIT_BURST` | `20` | Burst capacity |

### Database (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_DB_HOST` | | PostgreSQL host |
| `NEXUS_DB_PORT` | `5432` | PostgreSQL port |
| `NEXUS_DB_NAME` | | Database name |
| `NEXUS_DB_USER` | | Database user |
| `NEXUS_DB_PASSWORD` | | Database password |

### Watchdog

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXUS_WATCHDOG_SERVICES` | | Comma-separated services to monitor (e.g., `nginx,postgresql`) |
| `NEXUS_WATCHDOG_INTERVAL` | `30` | Health check interval (seconds) |
| `NEXUS_MAX_RESTART_ATTEMPTS` | `10` | Max auto-restarts per cooldown window |
| `NEXUS_RESTART_COOLDOWN` | `120` | Cooldown window (seconds) |

---

## Deployment

### Docker

```bash
docker compose up -d
```

### Systemd

```bash
./scripts/deploy-to-server.sh your-server-ip root 22
```

Creates a systemd service with auto-restart + nginx reverse proxy snippet.

### Behind Nginx

```nginx
location /mcp/nexus {
    proxy_pass http://127.0.0.1:8766/mcp;
    proxy_http_version 1.1;
    proxy_set_header Host $proxy_host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
}
```

> **Important:** Use `$proxy_host` (not `$host`) for the Host header when proxying to MCP Nexus. The MCP server validates the Host header and will reject requests with mismatched hosts.

---

## Authentication

MCP Nexus uses **SSH credentials as OAuth credentials** — your server IP is your client ID, your SSH password is your client secret.

### Gateway authentication (hosted at lightcap.ai)

```bash
# Authenticate with your server credentials
curl -X POST https://lightcap.ai/mcp/nexus/oauth/token \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "client_credentials",
    "client_id": "YOUR_SERVER_IP",
    "client_secret": "YOUR_SSH_PASSWORD"
  }'

# Response:
# {
#   "access_token": "abc123...",
#   "token_type": "bearer",
#   "expires_in": 3600,
#   "target": "root@YOUR_SERVER_IP:22"
# }
```

**Optional parameters:**
- `ssh_user` — SSH username (default: `root`)
- `ssh_port` — SSH port (default: `22`)

```bash
# Non-root user on custom port
curl -X POST https://lightcap.ai/mcp/nexus/oauth/token \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "client_credentials",
    "client_id": "203.0.113.50",
    "client_secret": "my-ssh-password",
    "ssh_user": "deploy",
    "ssh_port": 2222
  }'
```

### How it works

1. You send your server IP + SSH password
2. MCP Nexus validates by connecting to your server via SSH
3. If SSH succeeds, you get a token
4. All MCP tool calls with that token route to YOUR server
5. Each user gets their own isolated SSH pool

### Self-hosted mode

When running MCP Nexus on your own machine, no authentication is needed — it uses the SSH credentials from your `.env` file automatically.

---

## Development

```bash
git clone https://github.com/lightcap-ai/mcp-nexus.git
cd mcp-nexus
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest             # run tests
ruff check .       # lint
mypy mcp_nexus/    # type check
```

---

## License

MIT License - see [LICENSE](LICENSE) for details.

---

<p align="center">
  Built by <a href="https://lightcap.ai">Lightcap AI</a> &bull; Hosted at <a href="https://lightcap.ai/mcp/nexus">lightcap.ai/mcp/nexus</a>
</p>
