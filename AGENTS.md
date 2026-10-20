# AGENTS.md — Building the complete ABUS product

You are building **ABUS** (Omarchy Agent Bus), a local-first messaging bus for AI coding agents on Omarchy.

This file is the single source of truth for product intent, locked decisions, interfaces, and known gaps. Prefer it over reverse-engineering the current skeleton.

## Product intent

ABUS lets agents on an Omarchy machine (and across a Tailscale network of Omarchy machines) send structured messages to each other, with durable inboxes, explicit registration, and first-class Herdr integration.

It must feel native to Omarchy: Quickshell plugin, `omarchy` CLI surface, skills, MCP, and Tailscale.

## Locked decisions

| Decision | Choice |
|----------|--------|
| Scope | Omarchy-specific. Primary CLI is `omarchy abus`. |
| Daemon host | Quickshell service plugin (lives inside `omarchy-shell`). |
| Persistence | Durable SQLite inbox. |
| Registration | Explicit. Agents must register a name before they can receive. |
| Retention | 7 days or 1 000 messages per inbox, whichever first. |
| Herdr | Tight bridge. Addressable as `herdr:<pane-id>`. |
| Network | Tailscale Serve on the local socket. Identity via Tailscale. |
| Protocol | NDJSON over Unix socket `$XDG_RUNTIME_DIR/abus.sock` (mode 0600). |

## Current state (skeleton)

The repo contains a minimal working core:

- `plugin/Service.qml` starts a Python daemon (path is currently wrong for installed plugins).
- `plugin/bin/abusd.py` implements register / send / inbox / ack / list with SQLite.
- `bin/omarchy-abus` is a thin CLI client.
- Docs and skill sketches exist.
- Herdr discovery, Tailscale expose, and MCP are stubs.

Do not treat the current code as complete. Fix known bugs and implement the missing pieces below.

## Required deliverables

### 1. Correct Quickshell plugin

Omarchy installs third-party plugins to `~/.config/omarchy/plugins/<id>/`.

- `manifest.json` id must be `omarchy.abus`.
- Kind: `service`.
- `Service.qml` must locate `abusd.py` relative to the plugin directory, not `Quickshell.shellDir`.
- Restart the daemon if it exits.
- Log clearly to the shell console.

### 2. Daemon (`plugin/bin/abusd.py` or equivalent)

Must implement at least:

- `register` — `{name, capabilities?}` → creates durable agent record
- `unregister` — `{name}`
- `send` — `{to, type, payload, from?, corr?}` → stores message, returns id
- `inbox` — `{name?}` → returns unacked messages for that target
- `ack` — `{id}`
- `list` — registered agents + live Herdr panes
- `status` — health + socket path

Enforce registration for targets starting with `agent:`.

Retention: prune messages older than 7 days and keep only the newest 1 000 per `to_agent`.

Socket permissions: `0600`.

### 3. Addressing

- Registered agents: `agent:<name>`
- Herdr panes: `herdr:<pane-id>` (local) or `herdr:<machine>/<pane-id>`
- Broadcasts: `broadcast:status`, `broadcast:events`

### 4. Herdr tight bridge

Use Herdr’s CLI or socket API (prefer CLI wrappers for reliability).

- Discovery: surface panes from `herdr agent list --json` (or equivalent socket call) as `herdr:<id>`.
- Prompt injection: when `to` starts with `herdr:`, call `herdr agent prompt <pane> "..."` (or the socket equivalent).
- Status: periodically or via events, publish Herdr pane states as ABUS `status` messages on `broadcast:status`.

Document the exact Herdr commands/methods used.

### 5. CLI

`bin/omarchy-abus` (or packaged equivalent) must support:

```
register <name> [--capabilities ...]
unregister <name>
send <to> <type> [json-payload|-]
inbox [name]
ack <id>
list
machines
expose [on|off]
status
```

It should be usable as `omarchy abus ...` after install (document how, or provide a wrapper that Omarchy’s command system can pick up).

### 6. Tailscale Serve

`omarchy abus expose on` should:

- Enable Tailscale Serve for the ABUS socket (or a thin HTTP/WebSocket wrapper if Serve cannot proxy a raw Unix socket).
- Use Tailscale identity (no extra tokens).
- `machines` should list tailnet peers that expose ABUS (via `tailscale status --json` + probing or a well-known path).

Document the exact Serve command or API used.

### 7. MCP server

Provide a real MCP server (stdio or HTTP) that exposes at least:

- `abus_register`
- `abus_send`
- `abus_inbox`
- `abus_list`
- `abus_ack`
- `abus_machines`

So any Omarchy agent harness can use ABUS without custom code.

### 8. Skill

`skill/SKILL.md` (or the structure Omarchy expects) must teach agents:

- How to register
- How to send typed messages
- How to read and ack their inbox
- Correlation IDs
- Herdr addressing
- Prefer typed messages over free-form chat

Make it ready to be symlinked into Claude, Codex, Hermes, Pi, etc. skill directories the same way Omarchy does for its own skill.

### 9. Optional shell UI

A bar widget or panel (`omarchy.abus`) that shows:

- Registered agents
- Unread counts
- Herdr panes that are blocked

Nice-to-have, not required for v1.

## Protocol reference

See `docs/protocol.md`. Envelope:

```json
{
  "v": 1,
  "id": "...",
  "from": "agent:claude:main",
  "to": "agent:codex:reviewer",
  "type": "prompt",
  "corr": "optional",
  "ts": "...",
  "payload": {}
}
```

Message types: `prompt`, `reply`, `status`, `event`, `request`, `ack`.

## Testing expectations

- Daemon starts and creates the socket with correct permissions.
- Register → send → inbox → ack round-trip works.
- Unregistered `agent:` targets are rejected.
- Retention prunes correctly.
- `list` includes Herdr panes when Herdr is running.
- Plugin survives shell restart.
- Document a minimal manual test sequence.

## Known bugs in the skeleton (fix these)

1. `Service.qml` uses `Quickshell.shellDir + "/../plugin/bin/abusd.py"` — this path is wrong after `omarchy plugin add`.
2. CLI is not automatically available as `omarchy abus`.
3. Herdr list parsing assumes a specific JSON shape that may not match real `herdr agent list --json`.
4. No unregister method.
5. No Tailscale implementation.
6. MCP is a print stub.
7. No proper error responses on the socket for malformed requests.

## Style

- Keep the daemon small and dependency-light (stdlib Python is fine).
- Prefer explicit over magic.
- Document every external command (Herdr, Tailscale, Omarchy).
- Do not add a heavy framework.

## Success criteria

A user can:

1. `omarchy plugin add https://github.com/syndicalt/abus.git --enable`
2. `omarchy abus register my-agent`
3. Send a message to that agent (or a Herdr pane) from another process or agent.
4. Read the inbox and ack.
5. `omarchy abus list` shows both registered agents and Herdr panes.
6. Optionally expose over Tailscale and reach it from another machine on the tailnet.

When those work reliably, the product is complete for v1.
