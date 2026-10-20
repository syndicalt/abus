# Herdr idle-only submission

This local companion patch adds `herdr agent prompt-if-idle` and the distinct JSON method `agent.prompt_if_idle`. ABUS requires this operation for pane delivery. Existing `agent.prompt` behavior and frozen endpoint codecs are preserved; older servers reject the new method instead of silently ignoring a guard flag.

Base: upstream [Herdr v0.9.1](https://github.com/herdrdev/herdr/tree/v0.9.1), commit `065ef9d6a531c49fb8bee7e818ef837065b21ee9`. The patch is maintained here for a private build; it has not been submitted or published upstream. Herdr code and patch context remain under Apache-2.0; see [LICENSE](LICENSE).

Use a separate checkout and build directory. Herdr's pinned toolchain is Rust 1.96.1; its native dependency build also requires Zig 0.16.0. Install `just` and `cargo-nextest` for its validation recipes.

```bash
git clone --branch v0.9.1 --depth 1 https://github.com/herdrdev/herdr.git herdr-abus
cd herdr-abus
test "$(git rev-parse HEAD)" = 065ef9d6a531c49fb8bee7e818ef837065b21ee9
git apply --check /path/to/abus/integrations/herdr/0001-prompt-if-idle.patch
git apply /path/to/abus/integrations/herdr/0001-prompt-if-idle.patch
just test-one prompt
just build
```

Point `ABUS_HERDR_BIN` at the resulting binary, and run the selected Herdr session's server with that same build. `ABUS_HERDR_SESSION` selects its named session. This bundle does not replace the installed Herdr executable or restart an existing user's session. Verify it from the ABUS checkout:

```bash
ABUS_HERDR_BIN=/absolute/path/to/herdr-abus/target/release/herdr uv run python scripts/verify_herdr.py
```

The operation checks the server's current effective idle/done state and reserves the terminal before enqueueing any focus or prompt input. Another guarded submission is rejected while that reservation exists, including through another pane alias. A reservation clears only after pending writes finish and fresh authoritative working/blocked evidence follows the final Enter, or a verified process exit/replacement occurs. Idle observations and elapsed time alone do not clear it. Ambiguous write failures retain it; definitely rejected enqueueing releases its own ticket. Unmodified legacy submissions also participate in reservation tracking, while keeping their existing permission to submit to working agents.

This guards atomic acceptance against Herdr's observed runtime state. It cannot establish an agent's internal idleness, prove which prompt caused a later state report, or prevent concurrent manual input. An agent that never reports activity can remain reserved; ABUS keeps subsequent messages queued with the reported error. Prompt delivery remains at least once across crashes; IDs support receiver deduplication.

See [refactor validation](../../docs/refactor-validation.md) for actual checks and their limits. `just check` includes Windows cross lint and requires a separately provisioned Windows SDK; the local verification did not install an SDK or accept its license.
