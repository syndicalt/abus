Private Herdr companion: 0.9.5-abus.d5f7a850d84a

Upstream v0.9.5 commit 2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01. Patch SHA256 d5f7a850d84ae8e3ebd8983e78b526b23bab3308b0a5b3ea26a90b7d80e0cb44.

Build: PATH=/tmp/herdr-abus-tools:$PATH ZIG=/tmp/herdr-abus-tools/python/ziglang/zig CARGO_TARGET_DIR=/tmp/herdr-abus-marketplace-target HERDR_BUILD_CHANNEL=abus HERDR_BUILD_ID=d5f7a850d84a HERDR_BUILD_COMMIT=2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01 just build

Checks completed: generated_protocol_schema_artifact_is_current passed; just ci formatting/all-target clippy passed; reviewer independently passed 24 frozen/shape/reservation/guard/capability/handoff checks. just maintenance-test passed 150 Python and 5 workflow JS tests; just ui-hot-path-architecture-test passed 6 tests; just integration-assets-test passed 55 JS tests; just docs-contract-test passed 7; translation parity and git diff HEAD --check passed. Windows lint reports missing Windows SDK before compilation; no SDK/license installation performed.

Official stock 0.9.1 and 0.9.5 raw JSON live handoffs to this release passed. Acceptance files preserve evidence for the same shell/controlled actor PID, pane and socket path, inherited PTY input/output, live raw ping capability, failclosed state before fresh evidence, Unknown rejection, fresh Idle acceptance, literal payload, duplicate fence before Working, Working/Blocked rejection and pending-handoff refusal without child loss. These checks used only disposable configurations and sockets; no real running session or global binary was modified.

Full-suite verification:

- Normal full no-failfast run: 4,041 passed, 1 failed, 14 ignored (4,042 executed). Command: PATH=/tmp/herdr-abus-tools:$PATH ZIG=/tmp/herdr-abus-tools/python/ziglang/zig CARGO_TARGET_DIR=/tmp/herdr-abus-marketplace-target CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0 cargo nextest run --locked --no-fail-fast --test-threads 4 --status-level fail --final-status-level fail --failure-output final --success-output never.
- The single failure is machine_api_recovers_a_stale_path_before_sending_a_mutation: its fake remote PATH discovers installed /usr/bin/herdr, whose stock config/herdr path differs from the test's debug config/herdr-dev socket. A clean official v0.9.5 worktree reproduces the identical failure (baseline-machine-api.log/json).
- The same failing fixture passes under a private bwrap mount masking /usr/bin/herdr with /usr/bin/false. Full no-failfast bwrap run: 4,033 passed and 9 namespace ownership/ACL failures; those 9 all pass in the normal full run.
- Earlier parallel-load signal-startup and client-reconnect timing failures passed in the final normal full run. No source changes were made to dismiss these upstream/environment fixture failures. Full CI is not claimed green.

Final release artifact details and notices are in manifest.json. THIRD_PARTY_NOTICES.txt preserves 241 resolved crate declarations and available supplied files, vendored and Zig/native dependency notices, and toolchain standard-library supplied notices without inferring which terms apply. Four downloaded crates supplied no separate license/README notice; their package-declared identifiers remain in the inventory.
