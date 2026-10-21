# Owner-only gateway verification

Marketplace issue [#12437](https://github.com/omacom/omarchy-plugin-marketplace/issues/12437#issuecomment-6306066574) identified a cross-user local access flaw in `cf7ea31`: the default loopback HTTP listener trusted a supplied forwarding address after a valid WhoIs lookup. Another local UID could impersonate a known tailnet source to discover pane metadata or enqueue messages, even with Serve exposure disabled.

The 1.0.1 changes remove the TCP listener entirely. Tailscale Serve targets the existing private Unix HTTP socket. Every HTTP route requires an AF_UNIX connection with kernel `SO_PEERCRED` identifying the owner UID or root before consulting headers or WhoIs. The runtime directory remains 0700 and the socket 0600. Same-UID processes and root remain trusted.

Exact legacy routes can be conditionally migrated or removed with ETag protection. Other ports and mounts remain intact. Legacy routes are reported as requiring migration; they are not reported as verified disabled. Permission failures never create a TCP fallback. See [network setup](architecture.md#network) for Tailscale's separate local-administrator requirement.

## Checks run on 2026-10-20

- The new forged-header discovery regression returned HTTP 200 before the fix and 403 after it. It also checks sending and health, no WhoIs call, no disclosure and no enqueue.
- All 119 tests passed on Python 3.11 and 3.12. Ruff lint and format checks, runtime lock export comparison, and wheel/source builds passed.
- `scripts/verify_gateway_boundary.py` passed in a private user namespace mapping the owner to UID 0 and subordinate host UIDs starting at 100000 to different namespace UIDs. A child switched to UID/GID 1. Both the private directory and the 0600 socket independently denied access. After deliberately relaxing permissions, discovery, sending and health returned 403 on Unix and an intentionally attached TCP test site before WhoIs. The owner still received HTTP 200 over Unix; the production daemon had zero TCP sites.
- Serve fixture tests passed for the Unix target, exact legacy migration/removal, preservation of other ports/mounts, concurrent-change rejection, idempotent removal and permission failure without fallback.
- Temporary-HOME installed plugin, desktop/unit validation, native observer, installed MCP, SIGKILL recovery and retained-history removal passed. The stock marketplace add/update/remove test passed with real native activation and supervised services. These tests use controlled external adapters.
- The focused fix was backported to the existing local installation without enabling managed Herdr. After restarting only ABUS, TCP port 8744 was absent, exposure was disabled, all 13 existing direct/agent messages remained, and all four Herdr process IDs/executables were unchanged. Status broadcasts continued normal retention at 1,000 messages; the pre-change database and files were backed up.

## Live Tailscale limit

The host's real Tailscale 1.104.1 LocalAPI rejected the attempted dedicated Unix Serve route with HTTP 401 and its local-administrator error. No route was installed; the original empty Serve configuration was retained. This validates refusal without a fallback, but **does not establish a successful live HTTPS round trip for the new Unix backend**. That integration still requires an administrator-authorized test. Historical loopback-based tailnet checks do not validate this changed backend.
