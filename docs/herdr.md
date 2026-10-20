# Herdr bridge

Discovery uses exactly:

```bash
herdr agent list
```

Current Herdr already prints JSON. Its envelope is `{"id":"...","result":{"type":"agent_list","agents":[...]}}`. ABUS accepts this wrapper, an `agents`/`panes` object, or a plain array. It uses `pane_id`, `agent_status`, `agent`, and project/title fields. The installed Herdr schema can be inspected with `herdr api schema --json`.

Discovery refreshes every five seconds. Changed pane states are stored as `status` envelopes on `broadcast:status`; removals publish `offline`. Agents subscribe explicitly. Missing or incompatible Herdr reports an error in daemon health instead of silently appearing healthy.

Submission uses exactly:

```bash
herdr agent prompt PANE MESSAGE
```

The daemon passes an argv array, never a shell string. A direct `herdr:<pane-id>` target submits the complete JSON envelope with its ID and reply instructions. A registered `agent:<name>` with `--endpoint herdr:<pane-id>` gets an inbox notification instead, retaining the original message until the agent acknowledges it.

ABUS only submits to panes reporting `idle` or `done`. `working`, `blocked`, `unknown`, and absent panes remain queued with a visible reason and retry backoff. A successful CLI command means the prompt was submitted; it does not establish that an agent read or completed it. Crash recovery can cause duplicate prompt submission, so the envelope ID is included for deduplication.

ABUS does not create, close, or take over existing panes. Tests use controlled adapters rather than injecting prompts into a user's existing work.

Set `ABUS_HERDR_SESSION` in the user service environment to select a named Herdr session. The default follows Herdr's default session. From a Herdr pane, `uv run python scripts/verify_herdr.py` creates and removes a disposable named test session with a controlled stdin receiver; it does not start an LLM.
