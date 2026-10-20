#!/usr/bin/env python3
"""Exercise stock plugin add/update/remove with real QML, setup and systemd jobs.

The desktop shell is represented by an activation adapter, and unit names are
mapped to unique test names. Tailscale and Herdr are controlled fixtures. No
existing ABUS unit, Herdr session or Tailscale socket is used.
"""

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]


async def run(*args, env=None, timeout=120):
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
        raise RuntimeError(f"{args[0]} exited {process.returncode}: {out.decode()} {err.decode()}")
    return out.decode()


def executable(path, source):
    path.write_text("#!/usr/bin/python3\n" + source)
    path.chmod(0o755)


def adapters(root, env, prefix, manager_runtime):
    adapters = root / "adapters"
    adapters.mkdir()
    units = {
        name: prefix + name[4:]
        for name in ("abus.service", "abus-cleanup.service", "abus-cleanup.path", "abus-setup.service")
    }
    executable(
        adapters / "systemctl",
        f"""import os,sys,pathlib
units={units!r}
source=pathlib.Path(os.environ['XDG_CONFIG_HOME'])/'systemd/user'
target=pathlib.Path({str(manager_runtime)!r})/'systemd/user'
target.mkdir(parents=True,exist_ok=True)
if 'daemon-reload' in sys.argv:
    for name,mapped in units.items():
        if name=='abus-setup.service': continue
        if (source/name).is_file():
            content=(source/name).read_text()
            for old,new in units.items(): content=content.replace('Unit='+old,'Unit='+new)
            (target/mapped).write_text(content)
        else: (target/mapped).unlink(missing_ok=True)
# systemctl connects to the manager's private socket, independent of test data.
os.environ['XDG_RUNTIME_DIR']={str(manager_runtime)!r}
os.execv('/usr/bin/systemctl',['systemctl',*[units.get(arg,arg) for arg in sys.argv[1:]]])
""",
    )
    executable(
        adapters / "systemd-run",
        f"""import os,sys
args=[arg.replace('--unit=abus-setup.service','--unit={units["abus-setup.service"]}') for arg in sys.argv[1:]]
os.execv('/usr/bin/systemd-run',['systemd-run',*args])
""",
    )
    executable(
        adapters / "omarchy-shell",
        """import json,os,pathlib,sys
state=pathlib.Path(os.environ['HOME'])/'shell-events.json'
value=json.loads(state.read_text()) if state.exists() else {'enabled':False,'generation':0}
plugin=pathlib.Path(os.environ['HOME'])/'.config/omarchy/plugins/syndicalt.abus'
command=sys.argv[2]
if command=='listPlugins':
    print(json.dumps([{'id':'syndicalt.abus','enabled':value['enabled']}] if plugin.exists() else []))
    sys.exit(0)
if command=='enablePlugin': value['enabled']=True
if command=='setPluginEnabled': value['enabled']=sys.argv[4]=='true'
if value['enabled'] or command=='setPluginEnabled': value['generation']+=1
temporary=state.with_suffix('.tmp')
temporary.write_text(json.dumps(value));temporary.replace(state)
print('ok')
""",
    )
    executable(
        adapters / "uv",
        f"import os,sys,time\nif sys.argv[1:3]==['pip','sync']: time.sleep(2)\nos.execv({shutil.which('uv')!r}, ['uv',*sys.argv[1:]])\n",
    )
    executable(
        adapters / "herdr",
        """import json,sys
if sys.argv[1:]==['agent','list']: print(json.dumps({'result':{'agents':[]}}))
else: sys.exit(1)
""",
    )
    executable(
        adapters / "tailscale",
        "import json\nprint(json.dumps({'BackendState':'Running','Self':{'DNSName':'fixture.test.'},'Peer':{}}))\n",
    )
    env["PATH"] = str(adapters) + ":" + os.environ["PATH"]
    return units


async def verify(artifact=None):
    manager_runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    prefix = "abus-market-check-" + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="abus-market-") as directory:
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
            "ABUS_TAILSCALE_SOCKET": str(root / "tailscale.sock"),
            "QT_QPA_PLATFORM": "offscreen",
            "QS_NO_RELOAD_POPUP": "1",
            "OMARCHY_PATH": "/usr/share/omarchy",
            "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(Path.home() / ".cache/uv")),
            "UV_PYTHON_INSTALL_DIR": os.environ.get(
                "UV_PYTHON_INSTALL_DIR", str(Path.home() / ".local/share/uv/python")
            ),
            "UV_LINK_MODE": "copy",
        }
        env.pop("ABUS_HERDR_ARTIFACT", None)
        if artifact:
            env["ABUS_HERDR_ARTIFACT"] = str(artifact.resolve())
        for key in ("ABUS_HERDR_SESSION", "HERDR_SOCKET_PATH", "HERDR_CLIENT_SOCKET_PATH"):
            env.pop(key, None)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            env["ABUS_HTTP_PORT"] = str(reservation.getsockname()[1])
        units = adapters(root, env, prefix, manager_runtime)
        plugin = home / ".config/omarchy/plugins/syndicalt.abus"
        record_path = home / ".local/share/abus/installation.json"
        events = home / "shell-events.json"
        qml_root = root / "shell.qml"
        qml_root.write_text(
            f'import Quickshell\nimport "{os.path.relpath(plugin / "plugin", root)}" as Abus\nScope {{ Abus.Service {{}} }}\n'
        )
        qml_log = (root / "qml.log").open("w+")

        async def shell_driver():
            generation, process = -1, None
            try:
                while True:
                    state = (
                        json.loads(events.read_text())
                        if events.exists()
                        else {"generation": 0, "enabled": False}
                    )
                    if state["generation"] != generation:
                        generation = state["generation"]
                        if process and process.returncode is None:
                            process.terminate()
                            await asyncio.wait_for(process.wait(), 5)
                        if state["enabled"]:
                            process = await asyncio.create_subprocess_exec(
                                "qs",
                                "--path",
                                str(qml_root),
                                "--no-color",
                                env=env,
                                stdout=qml_log,
                                stderr=qml_log,
                            )
                    if process and process.returncode is not None and state["enabled"]:
                        raise RuntimeError("Native shell activation exited; inspect QML log")
                    await asyncio.sleep(0.05)
            finally:
                if process and process.returncode is None:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), 5)

        # A real Unix HTTP LocalAPI fixture prevents any host Serve mutations.
        calls = []

        async def serve_config(request):
            calls.append(request.method)
            assert request.method == "GET", "Disabled exposure should require no mutation"
            return web.json_response({}, headers={"Etag": '"fixture"'})

        local = web.Application()
        local.router.add_route("*", "/localapi/v0/serve-config", serve_config)
        local_runner = web.AppRunner(local)
        await local_runner.setup()
        await web.UnixSite(local_runner, env["ABUS_TAILSCALE_SOCKET"]).start()
        checkout = root / "checkout"
        shutil.copytree(
            ROOT,
            checkout,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"
            ),
        )
        await run("git", "init", "--initial-branch=main", checkout)

        async def commit(message):
            await run("git", "-C", checkout, "add", ".")
            await run(
                "git",
                "-C",
                checkout,
                "-c",
                "user.name=ABUS fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                message,
            )

        await commit("Initial fixture")
        http_root = root / "http"
        http_root.mkdir()
        bare = http_root / "repo.git"
        await run("git", "clone", "--bare", checkout, bare)
        await run("git", "--git-dir", bare, "update-server-info")
        http = web.Application()
        http.router.add_static("/", http_root)
        http_runner = web.AppRunner(http)
        await http_runner.setup()
        site = web.TCPSite(http_runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        driver = asyncio.create_task(shell_driver())
        cli = home / ".local/bin/omarchy-abus"

        async def call(*args):
            return json.loads(await run(cli, *args, env=env, timeout=5))

        async def pid():
            return (
                await run(
                    "/usr/bin/systemctl",
                    "--user",
                    "show",
                    units["abus.service"],
                    "--property=MainPID",
                    "--value",
                )
            ).strip()

        async def ready():
            async with asyncio.timeout(180):
                while True:
                    if driver.done():
                        await driver
                    try:
                        status = json.loads((home / ".local/share/abus/setup.json").read_text())
                        if status["phase"] == "failed":
                            raise AssertionError(status["error"])
                        if status["phase"] == "ready":
                            await call("list")
                            return
                    except (FileNotFoundError, RuntimeError):
                        pass
                    await asyncio.sleep(0.1)

        try:
            await run("omarchy-plugin-add", f"http://127.0.0.1:{port}/repo.git", "--enable", "--yes", env=env)
            # Destroy the native component/controller while its setup job runs.
            async with asyncio.timeout(30):
                while not (home / ".local/share/abus/setup.json").exists():
                    await asyncio.sleep(0.02)
            phase = json.loads((home / ".local/share/abus/setup.json").read_text())["phase"]
            assert phase == "installing", "Expected the controlled provisioning delay"
            await run("omarchy-shell", "shell", "rescanPlugins", env=env)
            await ready()
            await call("register", "marketplace-receiver")
            mid = (
                await call(
                    "send",
                    "agent:marketplace-receiver",
                    "request",
                    '{"text":"durable"}',
                    "--corr",
                    "market-check",
                )
            )["id"]
            first_pid = await pid()
            first_record = json.loads(record_path.read_text())
            # Reload real Service.qml; supervised setup must leave daemon PID alone.
            await run("omarchy-shell", "shell", "rescanPlugins", env=env)
            await asyncio.sleep(1)
            await ready()
            assert await pid() == first_pid, "Shell reload restarted the bus"
            assert (await call("message", mid))["corr"] == "market-check"
            with (checkout / "abus/__init__.py").open("a") as stream:
                stream.write("\n# Marketplace update fixture\n")
            await commit("Update fixture")
            await run("git", "-C", checkout, "push", str(bare), "main")
            await run("git", "--git-dir", bare, "update-server-info")
            await run("omarchy-plugin-update", "syndicalt.abus", "--yes", env=env)
            async with asyncio.timeout(120):
                while (
                    not record_path.exists()
                    or json.loads(record_path.read_text())["source"] == first_record["source"]
                ):
                    await asyncio.sleep(0.1)
            await ready()
            assert await pid() != first_pid, "Updated source did not restart the daemon"
            assert Path(first_record["releases"][0]).is_dir(), (
                "Update removed a runtime still used by clients"
            )
            assert (await call("message", mid))["id"] == mid
            await call("ack", mid, "--name", "marketplace-receiver")
            await run("omarchy-plugin-remove", "syndicalt.abus", "--yes", env=env)
            async with asyncio.timeout(30):
                while record_path.exists() or cli.exists():
                    await asyncio.sleep(0.1)
            assert not plugin.exists()
            assert (home / ".local/state/omarchy/abus/abus.db").is_file()
            assert calls and set(calls) == {"GET"}
            qml_log.flush()
            qml_log.seek(0)
            log = qml_log.read()
            assert not any(marker in log for marker in ("ERROR:", "ReferenceError", "TypeError")), log
            print(
                "Stock marketplace add/update/remove, native activation, supervised setup, reload continuity, durable mailbox and cleanup: PASS"
            )
        except BaseException:
            qml_log.flush()
            qml_log.seek(0)
            print(qml_log.read(), file=sys.stderr)
            for name in units.values():
                result = await run("journalctl", "--user", "-u", name, "-n", "35", "--no-pager")
                print(result, file=sys.stderr)
            raise
        finally:
            driver.cancel()
            await asyncio.gather(driver, return_exceptions=True)
            qml_log.close()
            for name in units.values():
                subprocess.run(
                    ["/usr/bin/systemctl", "--user", "disable", "--now", name],
                    capture_output=True,
                    check=False,
                )
                (manager_runtime / "systemd/user" / name).unlink(missing_ok=True)
            subprocess.run(
                ["/usr/bin/systemctl", "--user", "daemon-reload"], check=False, capture_output=True
            )
            await http_runner.cleanup()
            await local_runner.cleanup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--herdr-artifact", type=Path, help="Use an offline verified artifact instead of the release download"
    )
    asyncio.run(verify(parser.parse_args().herdr_artifact))
