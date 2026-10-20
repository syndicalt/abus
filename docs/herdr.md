# Herdr bridge

ABUS supplies a private, checksum-pinned Herdr runtime for Linux x86_64. Setup downloads the binary described in [runtime.json](../integrations/herdr/runtime.json), verifies SHA256 before making it executable, and records its provenance under `$XDG_DATA_HOME/abus/herdr/`. It does not replace `/usr/bin/herdr`.

Discovery uses `herdr agent list`; current Herdr prints JSON without a `--json` flag. ABUS accepts the current `result.agents` envelope and older agents/panes arrays. Pane status, name, project and title appear in discovery and changed states publish on `broadcast:status` for explicit subscribers.

ABUS resolves the selected server socket through `herdr status --json`, then sends a read-only JSON `ping` directly to that socket. Pane delivery requires protocol 22 and a literal `capabilities.agent_prompt_if_idle: true` in the running server's Pong. CLI help or schema is never capability evidence for the server.

For verified stock versions 0.9.1 and 0.9.5, ABUS revalidates the managed executable's checksum and requests `server.live_handoff` with its absolute private path, exact expected version and protocol. This transfers the existing PTYs and processes to the companion. ABUS verifies the replacement through a fresh socket connection before allowing delivery. Other stock versions remain visible but require a verified compatibility update; they are not handed off speculatively. A server already advertising the capability can be used without a managed handoff.

A 0.9.1 handoff may initially report an agent as unknown until fresh activity evidence arrives. Unknown panes cannot receive a prompt. The daemon reports `delivery_ready`, `runtime_version` and an actionable error through `omarchy abus status`; queued messages retain their IDs and errors through a failed or unsupported handoff. Local CLI/MCP mailboxes remain available.

## Submission

Submission uses the distinct JSON method:

```json
{"id":"request-id","method":"agent.prompt_if_idle","params":{"target":"w1:p3","text":"literal message"}}
```

ABUS sends bounded NDJSON over the local Unix socket; it never constructs a shell command from a payload. The server confirms `result.type: agent_prompted` after writing Enter. ABUS does not require a later activity state to claim submission, and submission never establishes task completion.

A direct `herdr:<pane-id>` destination submits the complete envelope and reply instructions. A registered `agent:<name>` with `--endpoint herdr:<pane-id>` receives an inbox notification, preserving the original message until acknowledgment. Notifications quote opaque IDs as literal CLI arguments.

ABUS refreshes the target before each attempt and serializes aliases of a pane. The companion checks its effective current idle/done state and reserves the terminal before enqueueing input. Working, blocked, unknown, absent and already reserved panes stay queued with errors and backoff. The reservation survives Enter until fresh authoritative working/blocked evidence after the final write. Idle observations and elapsed time alone do not clear it. Definitely rejected enqueueing releases its ticket; ambiguous write failures retain it. Legacy prompt operations participate in reservation tracking. A companion refuses a later live handoff while a reservation is unresolved.

This is atomic acceptance against Herdr's observed state. It cannot prove an agent's internal idleness, attribute a later state report to a particular prompt, or prevent manual input. An agent that never reports activity can remain reserved. Crashes may cause duplicate delivery, so receivers must deduplicate envelope IDs and send explicit correlated completion replies. ABUS never falls back to ordinary `agent.prompt`.

## Configuration and lifecycle

`ABUS_HERDR_SESSION` selects a named session, and `ABUS_HERDR_BIN` selects the discovery CLI. The default is `herdr` on PATH and its default session. Set these before installation so setup captures them in the generated service environment.

`python3 install.py --without-herdr` skips companion provisioning and sets `ABUS_HERDR_MANAGED=0` in the service, preventing managed handoffs. This choice is retained across automatic setup and updates; `--with-herdr` enables management later. Compatible externally managed servers still support pane delivery. For offline setup, use `--herdr-artifact /path/to/executable` or `ABUS_HERDR_ARTIFACT`; the pinned checksum remains mandatory.

Removing ABUS stops its daemon and cleanup watcher after exposure is verified off. It does not stop Herdr or delete the private Herdr executable: a session may still be running it. Retain that directory until every session using it is closed or deliberately migrated to another verified runtime.

[Companion provenance and build instructions](../integrations/herdr/README.md) describe the source port. `scripts/verify_herdr.py` verifies real guarded delivery in a disposable named session with a controlled receiver. `scripts/verify_managed_herdr.py` verifies ABUS's actual provisioning and handoff path against a supplied stock executable. Neither targets an existing user's panes.
