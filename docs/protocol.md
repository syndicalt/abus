# ABUS Protocol (v1)

Newline-delimited JSON over Unix socket.

## Request

```json
{"id": "1", "method": "send", "params": {"to": "agent:foo", "type": "prompt", "payload": {"text": "hi"}}}
```

## Methods

- `register` — `{name, capabilities?}`
- `send` — `{to, type, payload, from?, corr?}`
- `inbox` — `{name?}`
- `ack` — `{id}`
- `list`

## Message types

`prompt`, `reply`, `status`, `event`, `request`, `ack`

## Addressing

- `agent:<registered-name>`
- `herdr:<pane-id>`
- `broadcast:status`
- `broadcast:events`
