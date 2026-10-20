# Turnkey setup validation

Validated on 2026-10-20 on Omarchy, branch `feat/turnkey-marketplace-setup`, based on ABUS `ef806ecb72414e57302a9eb4ccd7872fd54c7d54`. Runtime/setup source fingerprint: `b9489ce4d73c61b404ead77c2a60a750ce63591a17063160d0e3d2fc21850674` (`abus.installation.source_digest`). This identifies runtime inputs independently of later documentation and merge commits.

- Python 3.12 and 3.11: 107 tests passed in each environment.
- Ruff lint/format and QML formatting passed. Wheel and source distributions built.
- `scripts/verify_installed.py`: real temporary-HOME install, generated plugin/desktop/unit validation, native observer without receipt or presence changes, installed MCP, SIGKILL recovery, and retained history after removal passed.
- `scripts/verify_marketplace.py`: stock add/update/remove against a temporary HTTP git fixture, actual native service activation and supervised systemd jobs passed. Closing the native controller during provisioning did not stop setup. Reload preserved daemon PID; update changed runtime and retained the same message; removal cleaned up with history retained. Shell activation and unit names were adapted for isolation, Herdr discovery was controlled, and a private LocalAPI fixture accepted only GET requests.
- `scripts/verify_managed_herdr.py`: ABUS's real managed handoff passed for official stock 0.9.1 and 0.9.5, preserving child PID, pane and PTY input/output and verifying live idle-only capability afterward.
- `scripts/verify_herdr.py`: real private companion discovery, literal prompt delivery, duplicate fencing before working, and working/blocked queue retention passed in a disposable session.

The private companion is `0.9.5-abus.d5f7a850d84a`, based on upstream `2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`. Binary SHA256 is `faab4128736d1691fb3407c1719b3d18dd2abc95d9f8da17df591d0d5a0c77d9`; patch SHA256 is `d5f7a850d84ae8e3ebd8983e78b526b23bab3308b0a5b3ea26a90b7d80e0cb44`. Packaging verified binary, patch, notices and version against the committed manifests.

Herdr's full normal Rust run passed 4,041 tests with 14 ignored and one failure also reproduced on clean upstream. Independent focused checks, Linux Clippy, schema, maintenance, architecture, assets and docs passed. [Commands and upstream comparison](validation/turnkey/validation.md) document the environmental failure and the missing Windows SDK. [0.9.1 actor evidence](validation/turnkey/acceptance-0.9.1-agent.json) and [0.9.5 actor evidence](validation/turnkey/acceptance-0.9.5-agent.json) record real guarded state and handoff behavior.

No existing user's Herdr sessions or live ABUS service were changed by these checks. A physical second-machine tailnet test was not repeated for this setup change. Earlier network evidence remains associated with its original revisions in [refactor validation](refactor-validation.md).
