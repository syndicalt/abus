# ABUS — Omarchy Agent Bus

**ABUS** is a lightweight, local-first messaging bus for AI agents on [Omarchy](https://omarchy.org/).

It provides durable inboxes, explicit agent registration, a tight Herdr bridge, and Tailscale Serve for networked agent communication — all living inside the Omarchy shell.

## Features (MVP)

- Quickshell service plugin that owns the bus
- Unix socket NDJSON protocol at `$XDG_RUNTIME_DIR/abus.sock`
- Durable SQLite inbox under `$XDG_STATE_HOME/omarchy/abus/`
- Explicit registration required before receiving
- Retention: 7 days or 1 000 messages per inbox
- Tight Herdr bridge (CLI-backed in MVP)
- `omarchy abus` CLI
- MCP server
- Agent skill
- Tailscale Serve exposure

## Install

```bash
omarchy plugin add https://github.com/syndicalt/abus.git --enable
```

Then restart the shell or reload plugins. The service starts automatically.

## Quick start

```bash
# Register an agent
omarchy abus register claude-main

# Send a prompt
omarchy abus send agent:claude-main prompt '{"text":"Review the open PR"}'

# Check inbox
omarchy abus inbox claude-main

# List agents (includes Herdr panes)
omarchy abus list

# Expose over Tailscale
omarchy abus expose on
```

## Documentation

- [Architecture](docs/architecture.md)
- [Protocol](docs/protocol.md)
- [CLI](docs/cli.md)
- [Herdr Bridge](docs/herdr.md)

## License

MIT
