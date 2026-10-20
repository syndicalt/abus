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

## Tailscale

On two authorized test machines, enable exposure on the receiver, register a dedicated receiving agent, and discover it from the sender. Send with an explicit ID, repeat that ID, and check that the receiver has one envelope and a machine-qualified return address. Take the receiver offline and verify the sender keeps a queued outbox item and retries after recovery. Reply with the original correlation ID. Disable only the ABUS handler afterward.

A same-machine test through the actual Tailscale HTTPS URL verifies Serve configuration, certificates, forwarding headers, LocalAPI source identity, and gateway acceptance. It does not establish connectivity from a separate physical machine; the two-instance automated test separately covers routing and retry semantics.

`scripts/verify_virtual_peer.py --meta <metadata.json>` runs the real HTTPS round trip and outage recovery against a prepared disposable VM. Its metadata identifies the SSH key and known-hosts directory. The VM needs a restartable `abus-validation` unit, an authorized Tailscale node, and a Tailscale state directory for certificates. See the script's module documentation for expected paths.

## Verified on 2026-10-20

The working tree based on `c8a6bae` passed all 40 automated tests on Omarchy with Python 3.12 and Quickshell installed. Ruff, formatting, plugin validation, desktop-file validation, systemd unit validation, and wheel/source builds passed. An isolated wheel installation opened the native monitor and connected through the MCP SDK.

Live checks passed for Herdr discovery and controlled pane submission, persistence after killing the daemon, systemd recovery, and delivery service survival through a full Omarchy shell restart. A separate Ubuntu 24.04 KVM guest passed real Tailscale HTTPS discovery, message delivery, source-node identity, duplicate suppression, correlated reply, acknowledgments, offline queueing, and automatic retry after service recovery. The guest was logged out and deleted afterward; host ABUS exposure was disabled.

These checks cover one Omarchy desktop and one Ubuntu VM. A second physical Omarchy desktop and upstream GitHub Actions were not exercised.

## Removal

`python3 uninstall.py` removes managed application files, service, widget, dispatcher wrapper, and skill links. SQLite history is retained. Remove separately any harness MCP configuration entries added outside the installer.
