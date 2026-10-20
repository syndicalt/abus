#!/usr/bin/env python3
"""Validate a real staged install in a temporary HOME and dedicated systemd unit."""

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


async def run(*args, env=None, timeout=30):
    process = await asyncio.create_subprocess_exec(
        *map(str, args), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(f"{args[0]} exited {process.returncode}: {err.decode()} {out.decode()}")
    return out.decode()


async def verify():
    with tempfile.TemporaryDirectory(prefix="abus-install-") as directory:
        root = Path(directory)
        home, runtime = root / "home", root / "run"
        home.mkdir()
        runtime.mkdir(mode=0o700)
        env = {
            **os.environ,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "XDG_RUNTIME_DIR": str(runtime),
            "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(Path.home() / ".cache/uv")),
            "UV_PYTHON_INSTALL_DIR": os.environ.get(
                "UV_PYTHON_INSTALL_DIR", str(Path.home() / ".local/share/uv/python")
            ),
            "UV_LINK_MODE": "copy",
            "QT_QPA_PLATFORM": "offscreen",
            "QS_NO_RELOAD_POPUP": "1",
        }
        # Only the dedicated session is visible to the adapter; no prompts are sent.
        env["ABUS_HERDR_SESSION"] = "abus-install-" + uuid.uuid4().hex[:8]
        for key in ("HERDR_SOCKET_PATH", "HERDR_CLIENT_SOCKET_PATH"):
            env.pop(key, None)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            env["ABUS_HTTP_PORT"] = str(reservation.getsockname()[1])
        await run(sys.executable, ROOT / "install.py", "--no-start", "--without-herdr", env=env, timeout=120)
        target = home / ".config/omarchy/plugins/syndicalt.abus"
        cli = home / ".local/bin/omarchy-abus"
        record = json.loads((home / ".local/share/abus/installation.json").read_text())
        python = Path(record["environments"][0]) / "bin/python"
        installed_runtime = Path(record["releases"][0])
        await run("omarchy-plugin-validate", target)
        await run("desktop-file-validate", home / ".local/share/applications/syndicalt.abus.desktop")
        await run("systemd-analyze", "--user", "verify", home / ".config/systemd/user/abus.service")

        async def call(*args):
            return json.loads(await run(cli, *args, env=env))

        unit = "abus-install-check-" + uuid.uuid4().hex[:8]
        qml, qml_log = None, None
        try:
            settings = [
                f"--setenv={key}={value}"
                for key, value in env.items()
                if key in {"HOME", "ABUS_HTTP_PORT", "ABUS_HERDR_SESSION"} or key.startswith("XDG_")
            ]
            await run(
                "systemd-run",
                "--user",
                "--quiet",
                "--collect",
                "--unit=" + unit,
                "--property=Restart=on-failure",
                "--property=RestartSec=100ms",
                *settings,
                python,
                installed_runtime / "plugin/bin/abusd.py",
            )
            for _ in range(100):
                try:
                    await call("status")
                    break
                except RuntimeError:
                    await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Installed service did not start")
            await call("register", "installed-check")
            mid = (await call("send", "agent:installed-check", "request", '{"text":"installed"}'))["id"]
            await call("claim", "installed-check", "--owner", "installed-receiver", "--lease", "300")
            database = home / ".local/state/omarchy/abus/abus.db"

            def snapshot():
                with sqlite3.connect(database) as connection:
                    return (
                        connection.execute("SELECT * FROM receipts").fetchall(),
                        connection.execute("SELECT seen FROM agents").fetchall(),
                    )

            before = snapshot()
            qml_log = (root / "qml.log").open("w+")
            qml = await asyncio.create_subprocess_exec(
                "qs",
                "--path",
                str(target / "ui/shell.qml"),
                "--no-color",
                env=env,
                stdout=qml_log,
                stderr=qml_log,
            )

            async def ipc(*args):
                return await run(
                    "qs",
                    "ipc",
                    "--path",
                    target / "ui/shell.qml",
                    "--any-display",
                    "call",
                    "abus",
                    *args,
                    env=env,
                )

            for _ in range(80):
                try:
                    if json.loads(await ipc("status"))["connected"]:
                        break
                except RuntimeError:
                    pass
                await asyncio.sleep(0.05)
            else:
                qml_log.seek(0)
                raise RuntimeError("Installed monitor did not connect: " + qml_log.read())
            await ipc("inbox", "installed-check")
            for _ in range(80):
                if json.loads(await ipc("status"))["messages"] == 1:
                    break
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Installed monitor did not show inbox")
            await ipc("inspect", mid)
            assert json.loads(await ipc("status"))["selectedMessage"] == mid
            assert snapshot() == before, "Monitor changed receipts or presence"
            qml.terminate()
            await asyncio.wait_for(qml.wait(), 5)
            qml_log.seek(0)
            log = qml_log.read()
            assert not any(marker in log for marker in ("ERROR:", "ReferenceError", "TypeError")), log

            mcp_config = json.loads((home / ".local/share/abus/mcp.json").read_text())["mcpServers"]["abus"]
            server = StdioServerParameters(command=mcp_config["command"], args=mcp_config["args"], env=env)
            async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
                await session.initialize()
                response = await session.call_tool("abus_message", {"id": mid})
                assert not response.isError
                assert json.loads(response.content[0].text)["id"] == mid

            async def pid():
                return (
                    await run("systemctl", "--user", "show", unit, "--property=MainPID", "--value")
                ).strip()

            old_pid = await pid()
            await run("systemctl", "--user", "kill", "--signal=KILL", unit)
            for _ in range(100):
                try:
                    new_pid = await pid()
                    if new_pid not in {"0", old_pid}:
                        assert (await call("message", mid))["id"] == mid
                        break
                except RuntimeError:
                    pass
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError("Installed service did not recover after SIGKILL")
            await call("ack", mid, "--name", "installed-check", "--owner", "installed-receiver")
            assert not (await call("inbox", "installed-check", "--observer"))["messages"]
            print(
                "Installed plugin, desktop/unit validation, read-only monitor, MCP and SIGKILL recovery: PASS"
            )
        finally:
            if qml and qml.returncode is None:
                qml.terminate()
                await asyncio.wait_for(qml.wait(), 5)
            if qml_log:
                qml_log.close()
            subprocess.run(["systemctl", "--user", "stop", unit], check=False, capture_output=True)
        await run(sys.executable, ROOT / "uninstall.py", "--files-only", env=env)
        assert database.is_file(), "Uninstall removed message history"
        assert not target.exists() and not cli.exists()
        print("Owned-file removal and retained history: PASS")


if __name__ == "__main__":
    asyncio.run(verify())
