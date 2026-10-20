# Architecture

One systemd user service owns ABUS. Python asyncio serves local NDJSON, coordinates asynchronous Herdr/Tailscale adapters, and owns SQLite. The native Quickshell application and shell widget are observers. CLI and stdio MCP processes share the same daemon.

## Files and lifetime

| Resource | Default |
|---|---|
| Local API socket | `$XDG_RUNTIME_DIR/abus.sock`, 0600 |
| HTTP socket | `$XDG_RUNTIME_DIR/abus-http.sock`, 0600 |
| HTTP backend | `127.0.0.1:8744` |
| Database | `$XDG_STATE_HOME/omarchy/abus/abus.db` |
| Installed code | `$XDG_CONFIG_HOME/omarchy/plugins/syndicalt.abus` |
| Dependencies | `$XDG_DATA_HOME/abus/venv` |
| Unit | `$XDG_CONFIG_HOME/systemd/user/abus.service` |

XDG state/data/config default to `~/.local/state`, `~/.local/share`, and `~/.config`. Without an XDG runtime directory, a private `/tmp/abus-<uid>` directory is used. Storage directories are 0700. SQLite uses WAL; one daemon owns writes. An advisory lock prevents a second daemon from replacing a live socket. Only stale sockets are removed; ordinary files are preserved.

systemd restarts the daemon after failure. Workers are supervised. Quickshell may reload independently. Shutdown cancels external subprocesses and closes sockets. Persistent messages, receipts, subscriptions, and retry schedules survive restarts.

## Delivery

`send` validates and persists a versioned envelope and its receipts in a transaction. Explicit IDs deduplicate retrying sends; reuse with changed content fails. Local agents receive immediately in their inbox. Optional Herdr endpoints cause an idle-pane inbox notification. Direct Herdr targets queue the complete envelope. Remote targets enter a local durable outbox.

Receivers claim messages exclusively with leases. `listen` renews during work, acknowledges exit 0, and releases on failure. Redelivery after a crash is possible. Receiver side effects must be idempotent. An acknowledgment records handling; task success is an explicit reply with the request's correlation ID.

Transient delivery failures retry with exponential backoff capped at 300 seconds. Permanent errors remain failed until an agent retries them. Queued messages do not overtake earlier queued messages for the same destination. Messages expire after seven days or when displaced by the newest 1,000 for their destination. The same retention bounds offline retries and deduplication.

Broadcast subscriptions create a receipt per subscriber for future messages. Each consumer has independent acknowledgment/claim state. Unsubscribing stops future deliveries; unregistering removes the agent's subscriptions but retains existing receipts until message retention removes them.

Registration and presence are distinct. An inbox poll, claim, or heartbeat refreshes a 90-second presence lease. Observer reads do not refresh presence, acknowledge, or claim. Minute traffic totals are stored separately so inbox count pruning does not erase traffic measurements.

## Network

The exact exposure command is:

```bash
tailscale serve --bg --https=8743 http://127.0.0.1:8744
```

To disable only ABUS's handler:

```bash
tailscale serve --bg --https=8743 off
```

ABUS checks existing Serve configuration before changing its port. It never resets other handlers. `ABUS_NETWORK_PORT` and `ABUS_HTTP_PORT` configure the tailnet HTTPS and local backend ports through the service environment. Receiving peers must agree on the discovery HTTPS port.

A loopback HTTP backend avoids Serve's requirement for privileged Unix-socket/path serving. Tailscale Serve supplies forwarded source headers. Before accepting a send or discovery request, ABUS resolves `X-Forwarded-For` through `/localapi/v0/whois?addr=<source>` on `/var/run/tailscale/tailscaled.sock`. The returned node DNS name determines the sender's namespace. This works for user-owned and tagged nodes. Local processes are trusted, matching Serve's localhost identity-header model; ABUS is designed for a personal Omarchy account.

The remote API offers `GET /v1/status`, authenticated `GET /v1/list`, and authenticated `POST /v1/send`. It cannot register recipients, read inboxes, acknowledge deliveries, change exposure, or relay a message to a third machine. Network reachability is governed by Tailscale grants. No agent token is needed.

`tailscale status --json` supplies known peers. ABUS probes online peers' well-known HTTPS endpoints concurrently with bounded deadlines. Only responding ABUS services are returned. Transport retries keep the original message ID, so a lost response does not duplicate the destination inbox.

## Native monitor

The standalone Quickshell window has its own `syndicalt.abus` app ID, a launcher entry, and Omarchy theme colors. Dashboard reads include health, presence, unread counts, minute traffic, and recent route endpoints. Inbox observer reads include active claims and optionally acknowledged messages. All retained inbox and activity pages can be browsed. The window exposes only read/navigation IPC methods. Agent management is available through CLI and MCP.
