# ABUS Skill

You are running on Omarchy with the ABUS agent bus available.

## How to use

1. Register yourself once:
   ```bash
   omarchy abus register my-agent-name
   ```

2. Send messages:
   ```bash
   omarchy abus send agent:other prompt '{"text":"please do X"}'
   ```

3. Check your inbox:
   ```bash
   omarchy abus inbox my-agent-name
   ```

4. Acknowledge:
   ```bash
   omarchy abus ack <message-id>
   ```

Prefer typed messages (`prompt`, `request`, `status`) over free-form. Always include a correlation ID when expecting a reply.

Herdr panes appear as `herdr:<pane-id>` in `omarchy abus list`.
