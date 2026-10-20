# ABUS Architecture

## Components

- **Quickshell service plugin** (`plugin/Service.qml`) — starts and supervises the daemon inside `omarchy-shell`.
- **Daemon** (`plugin/bin/abusd.py`) — Python asyncio Unix socket server + SQLite.
- **CLI** (`bin/omarchy-abus`) — thin client.
- **MCP** (`mcp/abus_mcp.py`) — optional MCP server.
- **Skill** — teaches agents how to use ABUS.

## Data

- Socket: `$XDG_RUNTIME_DIR/abus.sock` (0600)
- Store: `$XDG_STATE_HOME/omarchy/abus/abus.db`

## Registration

Agents must call `register` before they can receive messages addressed to `agent:<name>`.

## Herdr

MVP uses `herdr agent list --json` for discovery and can be extended to inject prompts via `herdr agent prompt`.

## Network

Tailscale Serve on the socket. Identity is provided by Tailscale.
