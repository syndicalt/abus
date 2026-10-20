# Managed Herdr companion

The companion adds `agent.prompt_if_idle` with a terminal reservation, the CLI command `herdr agent prompt-if-idle`, and a default-false JSON Pong capability. The running companion advertises `agent_prompt_if_idle: true`. Existing prompt semantics and frozen endpoint codecs remain intact. A pending prompt reservation prevents another live handoff from discarding the fence.

Upstream: [Herdr v0.9.5](https://github.com/herdrdev/herdr/tree/v0.9.5), commit `2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`. The private build is `0.9.5-abus.d5f7a850d84a`. The patch is maintained here and has not been submitted upstream. Herdr code and patch context are Apache-2.0; see [LICENSE](LICENSE) and [third-party notices](THIRD_PARTY_NOTICES.txt).

[runtime.json](runtime.json) pins the published raw executable URL and SHA256. [provenance.json](provenance.json) records source, patch, compiler and build identity. The installer verifies the raw binary without extracting an archive and installs it under a digest-named private data directory. Stock binaries and sessions are not stopped or replaced on disk. Live handoffs from stock 0.9.1 and 0.9.5 are verified; the live server must confirm the new capability before submission.

## Rebuilding

Use a separate checkout. The upstream toolchain is Rust 1.96.1; native dependencies require Zig 0.16.0. Install `just` and `cargo-nextest` for validation.

```bash
git clone --branch v0.9.5 --depth 1 https://github.com/herdrdev/herdr.git herdr-abus
cd herdr-abus
test "$(git rev-parse HEAD)" = 2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01
git apply --check /path/to/abus/integrations/herdr/0001-prompt-if-idle.patch
git apply /path/to/abus/integrations/herdr/0001-prompt-if-idle.patch
just ci
HERDR_BUILD_CHANNEL=abus HERDR_BUILD_ID=d5f7a850d84a \
  HERDR_BUILD_COMMIT=2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01 just build
```

The committed checksum identifies the tested executable, not a claim that every rebuild is byte-identical. Rebuilding requires recording and validating a new artifact before changing the runtime manifest. Package the raw executable, source patch, license, third-party notices and provenance together in the ABUS companion release. Do not change an existing release asset in place.

The supplied third-party inventory contains license identifiers and actual notice texts from locked Linux dependencies, vendored native sources and build inputs. It preserves supplied notices; it is not a separate legal certification.

## Validation

The port passed formatting, all-target Linux Clippy, the generated-schema check, and 24 independently reviewed capability, reservation, prompt, handoff, frozen-codec and method-shape checks. The normal full Rust run passed 4,041 tests with 14 ignored; one installed-Herdr fixture failure reproduced on clean upstream v0.9.5. That fixture passes with the global Herdr binary masked in an isolated namespace. The complete suite is not represented as green.

Maintenance passed 150 Python and five workflow JS checks; architecture six; assets 55; docs seven and translation parity. Windows cross lint requires an unavailable Windows SDK, so no Windows artifact is supplied.

Disposable stock 0.9.1 and 0.9.5 handoffs preserved pane/socket identity, child PIDs and PTY input/output. Controlled actors verified unknown-state rejection, fresh idle acceptance, literal payload delivery, a duplicate fence before observed working, working/blocked rejection and refusal to hand off with a pending reservation. No existing user's sessions or global binaries were modified. The earlier v0.9.1-only patch's historical results remain in [refactor validation](../../docs/refactor-validation.md).
