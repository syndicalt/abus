# ABUS product and development requirements

ABUS is a durable communication and management service for agents on Omarchy, locally and across Tailscale. People observe it in a native Quickshell application.

The user explicitly requires a read-only native monitor: people can read messages and view inboxes, including full payloads and acknowledged history. Do not add registration, send, acknowledgment, retry, or network-management controls to the app. Navigation, filters, pagination, text selection, and inspection are appropriate. Use plain, factual interface copy without slogans or promotional language.

## Architecture decisions

The user authorized building to the architectural recommendations in this session. These supersede the original skeleton's shell-owned daemon and reserved plugin ID:

- systemd user service owns one Python asyncio daemon per local user/runtime directory.
- Quickshell provides a native monitor and shell widget, independent of daemon lifetime.
- Third-party plugin ID: `syndicalt.abus`; root manifest. Omarchy reserves `omarchy.*`.
- SQLite owns durable identities, versioned envelopes, per-consumer receipts, subscriptions, delivery retries, and minute traffic counters.
- Explicit registration; durable identity and inbox survive a session's presence lease.
- NDJSON over `$XDG_RUNTIME_DIR/abus.sock`, mode 0600.
- Seven days or newest 1,000 messages per destination. Retention bounds delivery and deduplication.
- CLI and official Python MCP SDK expose communication and management.
- Herdr is an adapter for discovery, status, and idle-pane prompt submission.
- Tailscale Serve proxies HTTPS to a private loopback HTTP gateway. No new account or agent token system. Network exposure is optional.
- `aiohttp` handles HTTP; do not hand-roll HTTP or MCP framing. Keep dependencies pinned.

## Invariants

1. Store before accepting. Sender retries reuse message IDs; conflicting IDs fail explicitly.
2. Registration is required for local `agent:` targets. Support local agents, Herdr panes, remote machine-qualified agents/panes, and status/event broadcasts.
3. A broadcast has independent receipts for each subscriber. One subscriber's acknowledgment cannot hide it from another.
4. Receivers can claim deliveries with expiring leases. Renew during work, ack on success, release on failure. Reading in observer mode changes neither claims nor presence nor acknowledgments.
5. Accepted, notified, submitted, forwarded, and acknowledged describe different facts. Task completion requires an explicit reply.
6. Queue unavailable remote/pane destinations, retry with backoff, preserve message IDs, and surface errors. Preserve queued order per destination.
7. Submit to Herdr only when idle/done; never inject prompts into blocked or working panes. No arbitrary shell expansion of payloads.
8. Network gateway exposes health, discovery, and sending; registration, inbox reading, and management stay on the local socket. Verify source node through Tailscale LocalAPI; namespace sender addresses by source machine. No relaying remote requests onward.
9. Never reset global Tailscale Serve configuration or replace another application's handler. Only manage ABUS's dedicated port.
10. A shell reload must not stop delivery. A failed background worker must fail the daemon so systemd can restart it.

## Components

- `abus/store.py`: storage and domain rules; additive migration from skeleton databases.
- `abus/daemon.py`: local protocol, gateway, delivery workers, discovery, health.
- `abus/herdr.py`, `abus/network.py`: external adapters; document exact commands.
- `abus/client.py`, `abus/cli.py`, `abus/mcp.py`: client interfaces.
- `ui/`: native read-only monitor; monitor reads use `observer=true`.
- `plugin/`: shell service and unread widget.
- `install.py`, `uninstall.py`: user-owned installation only. Never edit `/usr/share/omarchy`.
- `skill/SKILL.md`: portable agent instructions.

## Validation

Run `uv run ruff check abus tests scripts install.py uninstall.py`, formatting checks, and `uv run pytest -q`. Use temporary stores/sockets and controlled receiver processes; never inject test prompts into an existing user's agent pane. Native UI tests must verify visible inbox access leaves receipt and presence data unchanged. Validate the packaged plugin without development virtualenv symlinks. Document any live integration not actually verified.

Required acceptance covers local registration/send/inbox/ack and restart persistence, malformed request errors, retention, claims and broadcast semantics, current Herdr discovery and submission, remote durable retry/deduplication and identity, MCP tool calls, native dashboard/inbox/message views, installation/launcher/widget, and independent service supervision.

Do not equate unit tests with live external integration tests. Do not claim a published upstream install is fixed until changes have actually been published. The current workspace can be built and installed independently of upstream.
