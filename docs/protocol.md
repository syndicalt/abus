# Local protocol v1

NDJSON over `$XDG_RUNTIME_DIR/abus.sock` (0600). Multiple requests may share a connection. Each request has one newline-terminated response. A request is limited to 256 KiB; payload objects are limited to 64 KiB when serialized as UTF-8 JSON. Strings must contain valid Unicode scalar values; NaN and infinity are rejected. Request IDs are null, a string of up to 128 characters, or a signed 64-bit integer. Clients must read through the response newline, not one fixed-size socket read.

```json
{"id":"request-1","method":"send","params":{"to":"agent:reviewer","from":"agent:coordinator","type":"request","corr":"review-1","payload":{"text":"Review this"}}}
```

Success: `{"id":"request-1","result":{"id":"message-id","state":"accepted","duplicate":false}}`.

Errors: `{"id":"request-1","error":{"code":"unregistered","message":"Recipient agent:reviewer is not registered"}}`. A malformed request whose ID cannot be read has `id:null`. Invalid parameters, conflicts, unknown methods, and unavailable integrations return explicit errors.

## Envelope

```json
{"v":1,"id":"message-id","from":"agent:coordinator","to":"agent:reviewer","type":"request","corr":"review-1","ts":1792512000,"payload":{"text":"Review this"},"state":"accepted","attempts":0,"last_error":null}
```

`ts` is Unix seconds assigned on persistence. Types: `prompt`, `reply`, `status`, `event`, `request`, `ack`. `corr` links application requests and replies; it is independent of the transport request ID. `send.params.id` is an optional stable message ID for idempotent retries.

Addresses: `agent:<name>`, `herdr:<pane-id>`, `agent:<machine>/<name>`, `herdr:<machine>/<pane-id>`, `broadcast:status`, and `broadcast:events`.

## Methods

| Method | Parameters |
|---|---|
| register | name; optional capabilities array, project, session, local herdr endpoint |
| unregister / heartbeat | name |
| send | to, type; optional payload object, from, corr, id |
| inbox | name; optional limit 1..100, after cursor, wait 0..25, observer, include_acked, max_bytes |
| claim | name, owner; optional limit, after, wait, lease 5..3600 seconds, max_bytes |
| ack / release / renew | id; name for broadcasts, owner for active claims; renew accepts lease 5..3600 |
| subscribe / unsubscribe | name, topic |
| list | no parameters |
| status / machines | no parameters |
| history | optional to, limit 1..100, before cursor, max_bytes |
| message | id; returns one complete retained envelope |
| retry | id of queued or failed delivery |
| expose | enabled boolean |
| dashboard | optional name, include_acked; always observes without changing presence/receipts |

`inbox`/`claim` return messages, next, more, total, owner. Advance `after=next` while `more=true`; start at zero on the next polling cycle. Observers can inspect active claims and each receipt's acknowledged/claimed flags. `claim` refuses observer/history mode. `history` returns newest first; pass `before=next` while more=true.

Pages default to a 1 MiB byte budget (`max_bytes`, 128 KiB..8 MiB), measured using the actual ASCII-escaped compact JSON representation with space reserved for response metadata. The page may contain fewer than `limit` messages. If the next envelope cannot fit, the request fails with the required budget instead of silently skipping it. `message` retrieves that envelope independently. Cursors are persisted sequence numbers and never reused after retention or restart.

`observer` and `include_acked` must be booleans. Claim renewal requires the live owner and extends the deadline without shortening it; an expired owner cannot renew or acknowledge a reclaimed delivery. Repeated acknowledgment of an already acknowledged receipt is idempotent.

Delivery states: `accepted` (local mailbox stored), `queued` (pending external delivery), `notified` (Herdr inbox notification submitted), `submitted` (direct pane submission succeeded), `forwarded` (remote bus accepted), `acked` (direct receiver acknowledged), `failed` (permanent delivery error). Broadcast acknowledgment is per receipt, not a global message state.

The API trusts processes with access to the local user's socket. Agent names are routing identities, not a local multi-tenant authorization boundary.
