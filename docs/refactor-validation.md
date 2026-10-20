# Refactor validation — 2026-10-20

Scope: ABUS, plus the authorized companion Herdr patch. ABUS baseline: `eee5d0719cf535ee15192faab3587a9a8b938a4d`. This report records local validation before publication; the active installation was not replaced. Publication and hosted CI status are recorded on the associated commit and pull request.

Reassessment: **quality 9/10; efficiency target met for the measured workload; low AI slop**. “10/10 efficiency” is interpreted as meeting the concrete budgets below while preserving durability, order, bounded resources and observer behavior. It is not a claim of universal optimality. Quality remains below perfection because state observations cannot prove an agent's internal idleness, and the new network implementation has not been tested between two physical machines.

The benchmark fingerprints all `abus/*.py` source as SHA256 `ba1924fbf02cfbed75417f5f390df098f691226e9594c3d12bc0be578012b17a`. Companion source is pinned by [metadata](../integrations/herdr/metadata.json); its patch SHA256 is `006537b5375dfa96050cd50b025b79cfe964f109fb1c3d969c0bdfecad516b20` against Herdr v0.9.1 commit `065ef9d6a531c49fb8bee7e818ef837065b21ee9`.

## Original findings

All thirteen review findings have implementation changes and regression coverage:

| Finding | Result and evidence |
|---|---|
| F1 Serve ownership | Exact structural root/listener ownership; conditional LocalAPI update. Unix LocalAPI tests cover unrelated mounts, URL substrings, other ports, null maps and concurrent configuration conflict. |
| F2 Receiver orphaning | SIGINT/SIGTERM reap the process group before releasing claims. Real signal tests verify exit codes, child death and immediate replacement claim. SIGKILL still requires lease recovery and receiver idempotence. |
| F3 Stale Herdr idle state | Fresh reads and pane locks plus a distinct atomic server operation. Herdr tests cover aliases, legacy pending input, delayed Enter, stale/duplicate completions, wait dispatch and cancellation. Isolated live checks reject a second prompt before working is observed and retain working/blocked deliveries. |
| F4 Unicode poisoning | Reject non-scalar Unicode and nonfinite payloads; emit ASCII-safe JSON. A real CLI reads a legacy surrogate envelope successfully. Escaped-byte pagination is checked before claiming. |
| F5 False forwarding success | Matching message ID, recognized state and boolean duplicate confirmation required; bounded JSON response reads. Real HTTP tests keep malformed/oversized “success” responses queued. |
| F6 Global retention cost | Destination-only count trimming, indexed expiry filters, bounded maintenance sweeps, transactional counters, persistent sequence cursors and recipient-specific wakeups. Read-only tests assert zero storage writes; benchmarks below exercise real receipts. |
| F7 Unsafe removal | Exposure inspection distinguishes unknown from disabled. Normal uninstall retains the installation if disabled exposure cannot be verified. |
| F8 Installer collisions | All generated targets checked before staging/publication; receipt hashes protect modified files. Immutable dependency environments and rollback tests cover provisioning and partial publication failure. Actual isolated installation/removal passes. |
| F9 Lease shortening | Renewal preserves the later deadline and fences expired owners. Tests cover long leases, expiry, replacement claims and repeated acknowledgment. |
| F10 Remote Herdr replies | Source-node qualification preserves both `agent:` and `herdr:` return addresses. Routing/identity tests pass. |
| F11 Global delivery blocking | Eight destinations progress concurrently with one delivery per destination and retained queue order. Independent-destination regression and slow-destination benchmark pass. |
| F12 Shared runtime | Existing runtime must be private, user-owned and not a symlink. Unsafe directory modes are refused without changing them. |
| F13 Executable metadata | Notification commands use shell-quoted argv and `--` before opaque IDs. Tests include substitutions, backticks, quotes and `--help`; payloads remain literal. |

The audit also added atomic migration rollback, full daemon SIGKILL/restart, worker-failure supervision, bounded subprocess output/exit waits, and a non-reading socket client regression. Socket close is bounded after a write timeout. Native JSON failures now remain visible instead of being silently swallowed. Formatting expanded compressed QML into ordinary components; migration, bounded JSON parsing and installation ownership have separate small modules. No new runtime dependencies, parallel framework, placeholder stubs or management controls were added to the monitor.

## Measured workload

Same host, Python 3.12.14, temporary SQLite on `/tmp` (tmpfs), WAL with FULL synchronization. Synthetic workload: 100 mailboxes, 100,000 retained 256-byte text payloads with real receipts, 25 operation samples, and 100 client tasks using the normal threaded Unix client. Some compilation/testing ran on the host during measurement; timings are observations, not hardware-independent limits. Baseline and refactor use the same benchmark script and workload. This is not a power-loss or storage-device throughput test.

| Operation | Baseline median | Refactor median | Refactor p95 | Budget |
|---|---:|---:|---:|---:|
| Send | 73.023 ms | 0.200 ms | 0.499 ms | p95 <1 ms |
| Inbox page | 78.824 ms | 1.515 ms | 1.609 ms | p95 <5 ms |
| History page | 74.750 ms | 1.146 ms | 1.193 ms | p95 <5 ms |
| Pending queue | 9.843 ms | 0.059 ms | 0.079 ms | p95 <1 ms |
| Maintenance check | 77.621 ms | 0.036 ms | 0.052 ms | p95 <1 ms |
| Dashboard | 195.031 ms | 3.481 ms | 3.705 ms | p95 <10 ms |

The 100-client batch decreased from **7,828 ms to 85.5 ms** (budget <250 ms); maximum sampled event-loop delay decreased from 1,878 ms to **24.1 ms** (budget <50 ms). A healthy destination behind 25 simulated 50 ms destinations decreased from 1,758 ms to **153.6 ms** (budget <250 ms). This measures adapter concurrency, not real tailnet latency.

Indexes, receipt sequences and counters cost storage: database size rose from 51.1 MB to 62.3 MB (22%), WAL high-water size from 52.3 MB to 63.6 MB, and measured peak RSS from 64.5 MiB to 67.0 MiB (4%). Both fit the assessment's 30% storage and 10% RSS growth budgets. No synchronous durability setting was weakened.

Scale checks with 1/1,000 and 10/10,000 mailboxes/messages also passed. With one mailbox and 1,000 near-maximum 65,000-byte text payloads, inbox p95 was **4.30 ms** (<10 ms), dashboard p95 **8.02 ms** (<20 ms), and an inbox page was 1,044,182 bytes (<1 MiB). Large pages stop on bytes rather than rendering every retained envelope.

Raw evidence: [baseline](benchmarks/baseline.json), [refactor](benchmarks/refactor.json), [large payload](benchmarks/large-payload.json). Run `scripts/benchmark.py` to reproduce; these budgets describe this assessment, not a production SLA or evidence about untested workloads.

## Checks actually run

- ABUS: **86 tests passed on Python 3.11.17 and 3.12.14**. Ruff lint/format, QML format comparison, runtime lock export comparison and distribution builds passed.
- Actual temporary-HOME installation provisioned pinned dependencies and passed plugin, desktop and systemd unit validation. Its installed native monitor inspected an active claim without changing receipts or presence; installed MCP retrieved the envelope. A dedicated transient systemd unit restarted automatically after SIGKILL with the same message and claim intact. Owned-file removal retained history.
- The patched Herdr binary passed live disposable-session discovery, literal submission, duplicate guarding and working/blocked queue retention. It never prompted an existing user's pane.
- Herdr `just ci`: **3,649 Rust tests passed, 6 ignored**, format/all-target clippy passed, plus 122 Python maintenance tests, 6 architecture tests and 44 integration/release-workflow JavaScript tests. After strengthening one test's acceptance assertion, final lint and all **42 prompt tests** passed; production code was unchanged by that last assertion. Seven docs contract tests passed. An independent reviewer checked frozen codecs, advertised method shapes, reservations and guarded dispatch. Frozen codecs and fixtures are unchanged.
- Companion patch cleanly applies to the pinned base index and reverse-applies to the tested tree. Apache-2.0 license and source provenance are bundled.

Windows cross lint was unavailable because its SDK was not provisioned; no SDK license was accepted. The new Serve update code passed controlled Unix LocalAPI ETag/conflict tests and actual read-only host inspection; this refactor did not mutate live exposure or repeat the earlier two-node HTTPS experiment. Local validation did not exercise a second physical Omarchy desktop, a new full desktop-shell restart, or GitHub-hosted/native CI. The optional native workflow needs an owner-configured runner. These limits are not represented as passed checks.
