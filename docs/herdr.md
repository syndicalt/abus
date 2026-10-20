# Herdr bridge

Discovery uses exactly:

```bash
herdr agent list
```

Current Herdr already prints JSON. Its envelope is `{"id":"...","result":{"type":"agent_list","agents":[...]}}`. ABUS accepts this wrapper, an `agents`/`panes` object, or a plain array. It uses `pane_id`, `agent_status`, `agent`, and project/title fields. The installed Herdr schema can be inspected with `herdr api schema --json`.

Discovery refreshes every five seconds. Changed pane states are stored as `status` envelopes on `broadcast:status`; removals publish `offline`. Agents subscribe explicitly. Missing or incompatible Herdr reports an error in daemon health instead of silently appearing healthy.

Submission uses exactly:

```bash
herdr agent prompt-if-idle PANE MESSAGE --wait --until working --until done --until blocked --timeout 5000
```

The daemon passes an argv array, never a shell string. A direct `herdr:<pane-id>` target submits the complete JSON envelope with its ID and reply instructions. A registered `agent:<name>` with `--endpoint herdr:<pane-id>` gets an inbox notification instead, retaining the original message until the agent acknowledges it.

ABUS refreshes the target before each attempt and serializes aliases of the same pane. The companion Herdr operation checks its current runtime state and reserves the terminal before enqueueing input. Only `idle`/`done` is accepted; `working`, `blocked`, `unknown`, absent, and already reserved panes remain queued with a visible reason and retry backoff. The reservation persists after Enter until fresh working/blocked evidence after the final write. It does not expire just because a timeout elapsed or another idle observation arrived. Legacy prompt submissions also reserve the terminal for guarded callers.

This is atomic acceptance against Herdr's observed state. Herdr cannot prove an agent's internal state, attribute a later state report to a particular prompt, or prevent concurrent manual input. A successful command confirms submission and the requested observed-state wait; task completion still requires a correlated reply. Crash recovery can cause duplicate prompt submission, so receivers must deduplicate by envelope ID.

The unmodified Herdr 0.9.1 API lacks this operation. A new RPC name (`agent.prompt_if_idle`) ensures an older server rejects the request instead of ignoring a policy flag. ABUS never falls back to ordinary `agent.prompt`. See [the companion patch](../integrations/herdr/README.md) for the pinned source revision, build and verification instructions.

ABUS does not create, close, or take over existing panes. Tests use controlled adapters rather than injecting prompts into a user's existing work.

Set `ABUS_HERDR_SESSION` in the user service environment to select a named Herdr session, and `ABUS_HERDR_BIN` to the companion binary's absolute path. The default executable is `herdr` on PATH. The selected session's server must also run the patched binary. From a Herdr pane, `ABUS_HERDR_BIN=/path/to/patched/herdr uv run python scripts/verify_herdr.py` creates and removes a disposable named test session with a controlled stdin receiver; it does not start an LLM. It checks a second submission before working is observed, literal payloads, and working/blocked queue retention.
