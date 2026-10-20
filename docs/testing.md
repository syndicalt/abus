# Verification

Run `uv sync --locked`, `uv run ruff check abus tests scripts install.py uninstall.py`, `uv run ruff format --check abus tests scripts install.py uninstall.py`, and `uv run pytest -q`.

Protocol and integration tests run real local sockets, a real MCP stdio client/server, independent CLI receiver processes, and two bus instances communicating over the HTTP gateway. External identity/discovery adapters are controlled in these tests. A lost remote response followed by retry must leave one destination envelope. Claims, retries, per-subscriber acknowledgments, retention, migration, large frames, pagination, and malformed inputs are covered.

If Quickshell is installed, native tests launch an offscreen window and use its read/navigation IPC to select an inbox and inspect a message. They assert that receipts and agent presence remain unchanged. If Omarchy is installed, the clean plugin package is validated. Machines without these applications explicitly skip those checks.

## Local installation

```bash
python3 install.py
systemd-analyze --user verify ~/.config/systemd/user/abus.service
systemctl --user is-active abus.service
omarchy abus status
omarchy abus ui
```

Check the launcher and bar widget, overview charts, message JSON, inbox filter modes, and previous/next pages. Change the Omarchy theme and verify the monitor follows it. Reading an inbox must leave its unread count and receiver presence unchanged.

For persistence, register a dedicated test name, send a message, restart the service, then read and acknowledge the same ID. A SIGKILL of the service should be followed by automatic restart with the inbox intact. Disable/re-enable only the ABUS plugin to exercise shell-component reload without touching the service; its PID should remain unchanged. A full desktop shell restart is a separate manual check and may disrupt the desktop session.

## Herdr

Compare `herdr agent list` with `omarchy abus list`. Pane names, IDs, status, and project should match. In a disposable test pane, send to `herdr:<id>` and verify receipt. Working or blocked panes must stay queued. Do not inject test prompts into existing work.

`uv run python scripts/verify_herdr.py` creates and removes an isolated Herdr session with a controlled receiver. It checks actual discovery, idle-pane submission, and literal argument handling without prompting an existing agent.

Setup provisions the [companion Herdr operation](../integrations/herdr/README.md). Set `ABUS_HERDR_BIN` to that build when testing an already compatible disposable session. `scripts/verify_managed_herdr.py --stock /path/to/stock-herdr --herdr-artifact /path/to/verified-companion` instead creates an isolated stock session and invokes ABUS’s real managed provisioning and handoff path. Run it for stock 0.9.1 and 0.9.5; it verifies live capability, pane/child identity and PTY I/O.

## Tailscale

On two authorized test machines, enable exposure on the receiver, register a dedicated receiving agent, and discover it from the sender. Send with an explicit ID, repeat that ID, and check that the receiver has one envelope and a machine-qualified return address. Take the receiver offline and verify the sender keeps a queued outbox item and retries after recovery. Reply with the original correlation ID. Disable only the ABUS handler afterward.

A same-machine test through the actual Tailscale HTTPS URL verifies Serve configuration, certificates, forwarding headers, LocalAPI source identity, and gateway acceptance. It does not establish connectivity from a separate physical machine; the two-instance automated test separately covers routing and retry semantics.

`scripts/verify_virtual_peer.py --meta <metadata.json>` runs the real HTTPS round trip and outage recovery against a prepared disposable VM. Its metadata identifies the SSH key and known-hosts directory. The VM needs a restartable `abus-validation` unit, an authorized Tailscale node, and a Tailscale state directory for certificates. See the script's module documentation for expected paths.

## Original verification on 2026-10-20

The working tree based on `c8a6bae` passed all 40 automated tests on Omarchy with Python 3.12 and Quickshell installed. Ruff, formatting, plugin validation, desktop-file validation, systemd unit validation, and wheel/source builds passed. An isolated wheel installation opened the native monitor and connected through the MCP SDK.

Live checks passed for Herdr discovery and controlled pane submission, persistence after killing the daemon, systemd recovery, and delivery service survival through a full Omarchy shell restart. A separate Ubuntu 24.04 KVM guest passed real Tailscale HTTPS discovery, message delivery, source-node identity, duplicate suppression, correlated reply, acknowledgments, offline queueing, and automatic retry after service recovery. The guest was logged out and deleted afterward; host ABUS exposure was disabled.

These checks cover one Omarchy desktop and one Ubuntu VM. A second physical Omarchy desktop and upstream GitHub Actions were not exercised.

## Refactor verification

The later refactor is recorded separately in [refactor validation](refactor-validation.md). Historical external checks above describe their original source revision; they do not establish live tailnet coverage for every later change.

`uv run python scripts/verify_installed.py` runs the actual installer under a temporary HOME, provisions pinned dependencies, validates the generated plugin/desktop/unit, starts a uniquely named transient systemd user unit, opens the installed native monitor offscreen, and calls the installed MCP adapter. It verifies that inbox inspection leaves claims, acknowledgments and presence unchanged, then kills only its own service with SIGKILL and checks automatic restart and the same retained message. It stops that unit and removes only its temporary install, retaining its temporary history until the test directory is cleaned up. It never reloads the user's shell or replaces the active ABUS service.

`uv run python scripts/benchmark.py` compares 100 mailboxes and 100,000 retained messages with real receipts. Use `--checkout /path/to/baseline` for the same workload against another source tree, `--rows`/`--receivers` for scale, or `--payload-bytes 65000 --rows 1000 --receivers 1` for near-maximum payloads. The script reports latency, a 100-client batch, event-loop delay, isolation from simulated slow destinations, database/WAL size and peak process RSS. Its temporary database uses the filesystem backing `/tmp`; these timings are not a disk-throughput or cross-machine latency guarantee.

Ubuntu CI checks Python 3.11 and 3.12, lint/format, tests, distribution builds and runtime lock consistency. The manually dispatched native workflow additionally requires an owner-configured Omarchy runner. Local success does not establish that GitHub runners were configured or executed.

## Marketplace lifecycle

`uv run python scripts/verify_marketplace.py --herdr-artifact /path/to/verified-companion` exercises the stock `omarchy-plugin-add`, `update` and `remove` commands against a private temporary git HTTP fixture. It uses real native Service.qml activation, real supervised setup/daemon/cleanup jobs, and an installed durable mailbox. It destroys the native setup controller during provisioning, reloads the shell component after readiness, upgrades source and removes the plugin. It verifies setup survival, an unchanged daemon PID on reload, a new runtime on update, retained messages and automatic cleanup with retained history.

A controlled shell activation adapter avoids loading the full desktop shell. Unit names are mapped to unique test names in the real user manager, and all are disabled/stopped and removed afterward. A private Tailscale LocalAPI fixture accepts only read requests; Herdr discovery is controlled. Thus this test does not establish real tailnet behavior or replace real Herdr handoff checks. It does not restart the actual ABUS service or existing Herdr sessions.

## Removal

`omarchy plugin remove syndicalt.abus` triggers supervised cleanup after the plugin disappears. `python3 uninstall.py` also supports explicit removal of managed application files, service, widget, dispatcher wrapper, and skill links. Exposure must be verified disabled; failures preserve files and supervised cleanup retries. Private Herdr executables are retained for any sessions still using them. SQLite history is retained. Remove separately any harness MCP configuration entries added outside the installer.
