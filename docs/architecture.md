# Architecture

One systemd user service owns ABUS. Python asyncio serves local NDJSON, coordinates asynchronous Herdr/Tailscale adapters, and owns SQLite. The native Quickshell application and shell widget are observers. CLI and stdio MCP processes share the same daemon.

## Files and lifetime

| Resource | Default |
|---|---|
| Local API socket | `$XDG_RUNTIME_DIR/abus.sock`, 0600 |
| HTTP socket | `$XDG_RUNTIME_DIR/abus-http.sock`, 0600 |
| Database | `$XDG_STATE_HOME/omarchy/abus/abus.db` |
| Plugin checkout | `$XDG_CONFIG_HOME/omarchy/plugins/syndicalt.abus` |
| Runtime code | `$XDG_DATA_HOME/abus/releases/<source-hash>` |
| Private Herdr | `$XDG_DATA_HOME/abus/herdr/<binary-sha256>/herdr` |
| Setup status | `$XDG_DATA_HOME/abus/setup.json` |
| Dependencies | `$XDG_DATA_HOME/abus/environments/<lock-hash>-py312` |
| Unit | `$XDG_CONFIG_HOME/systemd/user/abus.service` |

XDG state/data/config default to `~/.local/state`, `~/.local/share`, and `~/.config`. Without an XDG runtime directory, a private `/tmp/abus-<uid>` directory is used. Existing runtime directories must be user-owned, private, and not symlinks. Storage directories are 0700. SQLite uses WAL and FULL synchronization; one daemon owns writes. An advisory lock is acquired before opening or migrating the database. Only stale sockets are removed; ordinary files are preserved.

Plugin activation launches a transient `abus-setup.service` job that survives shell reloads. Setup holds a user-owned installation lock, verifies the companion artifact and stages dependencies and immutable runtime code before publishing generated files. An unchanged source/environment/configuration uses `systemctl start`, preserving an active daemon PID. An update publishes new runtime paths and restarts the daemon. The receipt tracks prior runtimes and environments for later cleanup.

`abus-cleanup.path` watches the plugin parent directory. After plugin removal, `abus-cleanup.service` runs the retained uninstaller under the installation lock, rechecks plugin absence, verifies exposure is disabled and removes owned files. Failure preserves files and retries after 30 seconds. Existing Herdr processes and their private executable remain available.

systemd restarts the daemon after failure. Workers are supervised. Quickshell may reload independently. Shutdown cancels external subprocesses and closes sockets. Persistent messages, receipts, subscriptions, and retry schedules survive restarts.

## Delivery

`send` validates and persists a versioned envelope and its receipts in a transaction. Explicit IDs deduplicate retrying sends; reuse with changed content fails. Local agents receive immediately in their inbox. Optional Herdr endpoints cause an idle-pane inbox notification. Direct Herdr targets queue the complete envelope. Remote targets enter a local durable outbox.

Receivers claim messages exclusively with leases. `listen` renews during work, acknowledges exit 0, and releases on failure. Redelivery after a crash is possible. Receiver side effects must be idempotent. An acknowledgment records handling; task success is an explicit reply with the request's correlation ID.

Transient delivery failures retry with exponential backoff capped at 300 seconds. Permanent errors remain failed until an agent retries them. Queued messages do not overtake earlier queued messages for the same destination. Messages expire after seven days or when displaced by the newest 1,000 for their destination. The same retention bounds offline retries and deduplication.

Up to eight destinations progress concurrently, with one in-flight delivery per destination. Local inbox readers wait on recipient-specific events or the next claim expiry. Sending trims only the affected destination. Expired messages are invisible immediately; maintenance deletes them in batches of 64. Reads do not perform retention writes. Transactional counters serve unread/state totals, and persisted sequence numbers keep pagination cursors monotonic through deletion and restart. Schema migrations commit atomically after a foreign-key check.

Broadcast subscriptions create a receipt per subscriber for future messages. Each consumer has independent acknowledgment/claim state. Unsubscribing stops future deliveries; unregistering removes the agent's subscriptions but retains existing receipts until message retention removes them.

Registration and presence are distinct. An inbox poll, claim, or heartbeat refreshes a 90-second presence lease. Observer reads do not refresh presence, acknowledge, or claim. Minute traffic totals are stored separately so inbox count pruning does not erase traffic measurements.

## Network

`omarchy abus expose on|off` uses LocalAPI `GET /localapi/v0/serve-config` and a conditional `POST` with the returned ETag in `If-Match`. A concurrent change returns a conflict for inspection and retry. Ownership requires an exact root proxy to the configured backend and an HTTPS listener; a matching substring or another mount is insufficient. Other ports and mounts are preserved. Inspection failure is reported as unknown exposure, and normal uninstall requires verified disabled exposure before removing files.

`ABUS_NETWORK_PORT` configures the tailnet HTTPS port. Receiving peers must agree on this discovery port. The backend is exclusively `$XDG_RUNTIME_DIR/abus-http.sock`; there is no TCP listener, even if `ABUS_HTTP_PORT` is set. That retired setting (default 8744) only identifies an exact legacy root proxy for conditional migration/removal. `expose on` replaces that root with the Unix proxy, preserving other mounts; `expose off` removes either exact owned root. Legacy routes are reported as requiring migration, never as verified disabled or healthy exposure. `ABUS_TAILSCALE_SOCKET` selects a nonstandard LocalAPI Unix socket; the default remains `/var/run/tailscale/tailscaled.sock`.

The HTTP Unix socket is 0600 inside the validated owner-only runtime directory. Every HTTP route first requires an AF_UNIX connection and Linux `SO_PEERCRED` with the daemon owner's UID or root. Missing/invalid credentials, other UIDs and TCP transports fail closed before WhoIs or request handling, including health requests. Root-owned tailscaled can access the socket. Processes running as the ABUS owner and root are trusted; other local users cannot supply identity headers to this backend.

Tailscale Serve supplies forwarded source headers. After checking the proxy connection, ABUS resolves `X-Forwarded-For` through `/localapi/v0/whois?addr=<source>` on the configured LocalAPI socket. The returned node DNS name determines the sender's namespace. This works for user-owned and tagged nodes.

Tailscale separately authorizes Unix-socket serving as a local-administrator operation ([upstream authorization](https://github.com/tailscale/tailscale/blob/main/ipn/localapi/serve.go)). Being an operator alone may be insufficient. Permission failures include the LocalAPI error and leave the backend private; ABUS never elevates, weakens permissions or falls back to TCP. An administrator can configure the exact route with `sudo tailscale serve --bg --https=8743 unix:/run/user/1000/abus-http.sock`, substituting the actual port and runtime path after checking port ownership. No global Serve reset is required. Local messaging works without network exposure.

The remote API offers `GET /v1/status`, authenticated `GET /v1/list`, and authenticated `POST /v1/send`. It cannot register recipients, read inboxes, acknowledge deliveries, change exposure, or relay a message to a third machine. Network reachability is governed by Tailscale grants. No agent token is needed.

`tailscale status --json` supplies known peers. Pooled HTTP connections and eight discovery workers bound resource use. Discovery has a 20-second deadline and a shared 15-second cache, with timestamps and partial-result errors. Only responding ABUS services are returned. Transport retries keep the original message ID, so a lost response does not duplicate the destination inbox. A successful forwarding response must confirm the same message ID, a recognized delivery state, and a boolean duplicate flag before the outbox is marked forwarded.

## Native monitor

The standalone Quickshell window has its own `syndicalt.abus` app ID, a launcher entry, and Omarchy theme colors. Dashboard reads include health, presence, unread counts, minute traffic, and recent route endpoints. Inbox observer reads include active claims and optionally acknowledged messages. All retained inbox and activity pages can be browsed. The window exposes only read/navigation IPC methods. Agent management is available through CLI and MCP.
