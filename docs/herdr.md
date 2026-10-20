# Herdr Bridge

## Discovery

`omarchy abus list` calls `herdr agent list --json` and surfaces panes as `herdr:<id>`.

## Prompt injection (planned / MVP stub)

```bash
herdr agent prompt <pane> "message"
```

ABUS can wrap this when `to` starts with `herdr:`.

## Status

Subscribe to Herdr events and republish as ABUS `status` messages.
