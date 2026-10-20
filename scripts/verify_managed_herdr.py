#!/usr/bin/env python3
"""Verify ABUS's actual managed-runtime handoff in a disposable stock session."""

import argparse
import asyncio
import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from abus.companion import prepare
from abus.herdr import Herdr

ROOT = Path(__file__).resolve().parents[1]


async def verify(stock, artifact):
    with tempfile.TemporaryDirectory(prefix="abus-managed-herdr-") as directory:
        root = Path(directory)
        runtime = root / "run"
        runtime.mkdir(mode=0o700)
        for key in list(os.environ):
            if key.startswith("HERDR_") or key == "ABUS_HERDR_SESSION":
                os.environ.pop(key)
        os.environ.update(
            XDG_RUNTIME_DIR=str(runtime),
            XDG_CONFIG_HOME=str(root / "config"),
            XDG_DATA_HOME=str(root / "data"),
            HERDR_SOCKET_PATH=str(runtime / "api.sock"),
            HERDR_CLIENT_SOCKET_PATH=str(runtime / "client.sock"),
            ABUS_HERDR_BIN=str(stock.resolve()),
            SHELL="/bin/sh",
        )
        config = root / "config/herdr"
        config.mkdir(parents=True)
        (config / "config.toml").write_text("onboarding = false\n")
        prepare(ROOT, artifact=artifact)
        log = (root / "server.log").open("w+")
        server = subprocess.Popen(
            [str(stock), "server"], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True
        )
        bridge = Herdr()
        bridge.socket = os.environ["HERDR_SOCKET_PATH"]
        try:
            async with asyncio.timeout(15):
                while True:
                    try:
                        before = await bridge.rpc("ping", timeout=1)
                        break
                    except Exception:
                        await asyncio.sleep(0.05)
            assert not bridge.capable(before), "The selected source must be stock Herdr"
            created = await bridge.rpc("workspace.create", {"cwd": str(root), "focus": True})
            pane = created["root_pane"]["pane_id"]
            pid_file, marker = root / "pid", root / "marker"

            async def input(text):
                await bridge.rpc("pane.send_input", {"pane_id": pane, "text": text, "keys": ["enter"]})

            await input(
                f"printf '%s' $$ > {shlex.quote(str(pid_file))}; printf before > {shlex.quote(str(marker))}"
            )
            async with asyncio.timeout(10):
                while not marker.exists() or not pid_file.exists() or not pid_file.read_text():
                    await asyncio.sleep(0.05)
            child = int(pid_file.read_text())
            await bridge.ensure_runtime()
            assert bridge.delivery_ready, bridge.error
            after = await bridge.rpc("ping")
            assert bridge.capable(after)
            assert any(item["pane_id"] == pane for item in (await bridge.rpc("pane.list"))["panes"])
            os.kill(child, 0)
            await input(
                f"printf '%s' $$ > {shlex.quote(str(pid_file))}; printf after >> {shlex.quote(str(marker))}"
            )
            async with asyncio.timeout(10):
                while marker.read_text() != "beforeafter":
                    await asyncio.sleep(0.05)
            assert int(pid_file.read_text()) == child
            print(
                json.dumps(
                    {
                        "stock": before["version"],
                        "managed": after["version"],
                        "same_child_pid": True,
                        "same_pane": True,
                        "pty_io_preserved": True,
                        "live_capability": True,
                    }
                )
            )
        finally:
            try:
                await bridge.rpc("server.stop", timeout=3)
            except Exception:
                pass
            try:
                await asyncio.to_thread(server.wait, 8)
            except subprocess.TimeoutExpired:
                server.kill()
                await asyncio.to_thread(server.wait, 3)
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--herdr-artifact", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.stock, args.herdr_artifact))
