# CLI

Use `omarchy abus --help` or `omarchy-abus --help`. Every data command prints one compact JSON object per line; `--pretty` before the command indents it. Failures print a structured error to stderr and exit nonzero.

```text
register NAME [--capabilities CAP ...] [--project PATH] [--session ID] [--endpoint herdr:PANE]
unregister NAME
heartbeat NAME
send TO TYPE [JSON|-] [--from NAME] [--corr ID] [--id STABLE-ID]
inbox [NAME] [--limit 100] [--after CURSOR] [--wait 25] [--max-bytes BYTES] [--observer] [--include-acked]
claim [NAME] [--owner ID] [--lease 60] [--limit 100] [--after CURSOR] [--wait 25] [--max-bytes BYTES]
ack ID [--name NAME] [--owner ID]
release ID [--name NAME] [--owner ID]
renew ID [--name NAME] [--owner ID] [--lease 60]
subscribe NAME broadcast:status|broadcast:events
unsubscribe NAME broadcast:status|broadcast:events
list
machines
status
history [--to ADDRESS] [--limit 100] [--before CURSOR] [--max-bytes BYTES]
message ID
retry ID
expose [on|off|status]
listen NAME [--once] --exec COMMAND [ARG ...]
ui
mcp
rpc METHOD [JSON-PARAMS]
```

`ABUS_NAME` supplies the default inbox and sender. Unprefixed `--from` names become `agent:<name>`. Use a registered return address when a reply is needed. `-` reads a JSON object from stdin. Do not embed shell commands in JSON payloads; arguments are passed literally to the receiving integration.

`listen` claims one envelope at a time and passes it on stdin to the command argv, without shell interpretation. Environment variables identify the delivery: `ABUS_NAME`, `ABUS_MESSAGE_ID`, and `ABUS_CORR`. The receiver uses its normal CLI/MCP interface to reply. Exit 0 acknowledges; other exits release the claim. SIGINT and SIGTERM terminate and reap the receiver process group before releasing its claim, with exit codes 130 and 143. SIGKILL cannot run cleanup; claims recover by expiry, and external effects still require idempotence by message ID. For long-running work, the listener renews every 20 seconds. Renewal never shortens a longer existing lease. `--once` exits after one handled attempt.

The native monitor uses `dashboard` and observer inbox reads. These are different from agent polls: they do not refresh presence or acquire claims. `rpc inbox '{"name":"reviewer","observer":true,"include_acked":true}'` exposes the same read-only view for scripts.

The installer creates the `omarchy abus` dispatcher wrapper in `~/.local/bin`; all other Omarchy commands are delegated unchanged to the packaged dispatcher. If another wrapper already exists, installation stops before changing files. Direct `omarchy-abus` invocation is always available after installation.
