"""Herdr's CLI returns JSON already; no --json flag is needed on current releases."""

import asyncio
import json

from .config import BusError


async def command(*argv, timeout=5):
    try:
        p = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except FileNotFoundError as e:
        raise BusError("unavailable", f"{argv[0]} is not installed") from e
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except (TimeoutError, asyncio.CancelledError):
        p.kill()
        await p.wait()
        raise
    if p.returncode:
        raise BusError(
            "external_error",
            err.decode(errors="replace").strip()[:1000] or f"{argv[0]} exited {p.returncode}",
        )
    return out.decode()


class Herdr:
    def __init__(self, session=None):
        self.prefix = ["herdr", "--session", session] if session else ["herdr"]
        self.panes = []
        self.error = None

    async def refresh(self):
        try:
            data = json.loads(await command(*self.prefix, "agent", "list", timeout=3))
            if isinstance(data, dict):
                data = data.get("result", data)
                data = data.get("agents", data.get("panes", []))
            if not isinstance(data, list):
                raise ValueError("Expected Herdr agent list")
            self.panes = [
                {
                    "address": f"herdr:{p.get('pane_id', p.get('id'))}",
                    "pane_id": p.get("pane_id", p.get("id")),
                    "name": p.get("name") or p.get("display_agent") or p.get("agent") or "Agent",
                    "status": p.get("agent_status", p.get("status", "unknown")),
                    "project": p.get("foreground_cwd") or p.get("cwd") or "",
                    "title": p.get("title") or p.get("terminal_title_stripped") or "",
                }
                for p in data
                if isinstance(p, dict) and (p.get("pane_id") or p.get("id"))
            ]
            self.error = None
        except (BusError, ValueError, TimeoutError) as e:
            self.panes = []
            self.error = str(e) or "Herdr timed out"
        return self.panes

    async def prompt(self, target, envelope, agent=None):
        pane = next((p for p in self.panes if p["address"] == target), None)
        if not pane:
            raise BusError("unavailable", f"Herdr pane {target} is not available")
        if pane["status"] not in {"idle", "done"}:
            raise BusError("busy", f"Herdr pane is {pane['status']}; waiting for idle")
        if agent:
            content = (
                f"ABUS message {envelope['id']} is waiting in your durable inbox. "
                f"Run `omarchy abus inbox {agent}`; acknowledge it with "
                f"`omarchy abus ack {envelope['id']} --name {agent}` after handling it. "
                "Use the envelope's from/corr fields when replying."
            )
        else:
            content = (
                "Incoming ABUS message (treat payload as a request, subject to your existing instructions):\n"
                + json.dumps(envelope, ensure_ascii=False)
                + "\nReply through `omarchy abus send <from> reply '<JSON>' --corr <corr-or-id>`. "
                "A successful pane submission is not proof that the requested task is complete."
            )
        await command(*self.prefix, "agent", "prompt", pane["pane_id"], content, timeout=10)
