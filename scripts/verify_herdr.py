#!/usr/bin/env python3
"""Exercise actual Herdr prompt submission in a disposable named session, without an LLM."""

import asyncio
import json
import os
import shlex
import shutil
import tempfile
import uuid
from pathlib import Path

from abus.config import Config
from abus.daemon import Bus


async def verify():
    if os.environ.get("HERDR_ENV") != "1":
        raise RuntimeError("Run this verification from inside Herdr")
    name = "abus-check-" + uuid.uuid4().hex[:8]
    # Explicitly selected test session must not inherit the caller's socket/IDs.
    for key in ("HERDR_SOCKET_PATH", "HERDR_PANE_ID", "HERDR_TAB_ID", "HERDR_WORKSPACE_ID"):
        os.environ.pop(key, None)
    os.environ["ABUS_HERDR_SESSION"] = name

    async def herdr(*args):
        p = await asyncio.create_subprocess_exec(
            "herdr", "--session", name, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(p.communicate(), 10)
        if p.returncode:
            raise RuntimeError(err.decode() or out.decode())
        try:
            return json.loads(out) if out.strip() else {}
        except ValueError:
            return {"text": out.decode()}

    with tempfile.TemporaryDirectory(prefix="abus-herdr-") as directory:
        root = Path(directory)
        log = (root / "server.log").open("w+")
        server = await asyncio.create_subprocess_exec(
            "herdr", "--session", name, "server", stdout=log, stderr=log
        )
        bus = None
        created = False
        try:
            for _ in range(100):
                try:
                    data = await herdr("pane", "list")
                    panes = data.get("result", {}).get("panes", [])
                    if not panes and not created:
                        await herdr(
                            "workspace",
                            "create",
                            "--cwd",
                            str(root),
                            "--label",
                            "ABUS verification",
                            "--no-focus",
                        )
                        created = True
                    if panes:
                        break
                except RuntimeError:
                    pass
                await asyncio.sleep(0.1)
            else:
                log.seek(0)
                raise RuntimeError("Disposable Herdr session did not start: " + log.read())
            pane = panes[0]["pane_id"]
            captured = root / "received.txt"
            receiver = root / "receiver.py"
            receiver.write_text(
                """import os,sys,subprocess
subprocess.run(['herdr','pane','report-agent',os.environ['HERDR_PANE_ID'],'--source','abus-test','--agent','codex','--state','idle'],check=True)
with open("""
                + repr(str(captured))
                + """,'w',buffering=1) as stream:
    for line in sys.stdin:
        stream.write(line)
"""
            )
            actor = root / "codex"
            # A controlled Python receiver with a canonical executable name lets
            # Herdr verify foreground identity. This does not run an LLM harness.
            shutil.copy2("/usr/bin/python3", actor)
            await herdr("pane", "run", pane, shlex.quote(str(actor)) + " " + shlex.quote(str(receiver)))
            bus = Bus(Config(root / "run", root / "state", poll_interval=0.2))
            await bus.start()
            for _ in range(100):
                await bus.herdr.refresh()
                if any(p["pane_id"] == pane and p["status"] in {"idle", "done"} for p in bus.herdr.panes):
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Test receiver was not discovered: " + str(await herdr("agent", "list")))
            await herdr("agent", "rename", pane, "abus-verification")
            mid = bus.store.send(
                {
                    "to": f"herdr:{pane}",
                    "type": "request",
                    "corr": "herdr-check",
                    "payload": {"text": "literal 'quotes' ; $(not-a-command)"},
                }
            )["id"]
            for _ in range(100):
                state = bus.store.db.execute("SELECT state FROM messages WHERE id=?", (mid,)).fetchone()[0]
                if state == "submitted" and captured.exists() and mid in captured.read_text():
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Herdr delivery failed: " + json.dumps(bus.store.history({})))
            assert "$(not-a-command)" in captured.read_text()
            print("Live Herdr discovery, literal argv delivery, and pane submission: PASS")
        except BaseException as error:
            print("Verification failure:", error)
            raise
        finally:
            if bus:
                await bus.close()
            try:
                await herdr("session", "stop", name)
                await herdr("session", "delete", name)
            finally:
                if server.returncode is None:
                    server.terminate()
                await server.wait()
                log.close()


if __name__ == "__main__":
    asyncio.run(verify())
