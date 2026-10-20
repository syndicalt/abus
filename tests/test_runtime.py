import asyncio
import json
import os
import shlex
import socket
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from abus.client import call
from abus.config import BusError, Config
from abus.herdr import Herdr, command

ROOT = Path(__file__).resolve().parents[1]


async def daemon_process(tmp_path, *, fail=False):
    config = Config(tmp_path / "runtime", tmp_path / "state")
    script = f"""import asyncio
from pathlib import Path
from abus.config import Config
from abus.daemon import Bus,run
Config.environment=classmethod(lambda cls:Config(Path({str(config.runtime)!r}),Path({str(config.state)!r})))
"""
    if fail:
        script += (
            "async def fail(self):\n raise RuntimeError('controlled worker failure')\nBus.maintenance=fail\n"
        )
    script += "asyncio.run(run())\n"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        cwd=ROOT,
        env={**os.environ, "PATH": str(tmp_path / "no-external-adapters")},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return process, config


async def wait_ready(config):
    async with asyncio.timeout(5):
        while True:
            try:
                await asyncio.to_thread(call, "list", config=config, timeout=0.2)
                return
            except BusError:
                await asyncio.sleep(0.01)


async def test_real_daemon_survives_sigkill_with_same_mailbox_and_message_id(tmp_path):
    process, config = await daemon_process(tmp_path)
    try:
        await wait_ready(config)
        await asyncio.to_thread(call, "register", {"name": "durable"}, config=config)
        mid = (
            await asyncio.to_thread(call, "send", {"to": "agent:durable", "type": "event"}, config=config)
        )["id"]
        process.kill()
        await process.wait()
        process, _ = await daemon_process(tmp_path)
        await wait_ready(config)
        inbox = await asyncio.to_thread(call, "inbox", {"name": "durable"}, config=config)
        assert [message["id"] for message in inbox["messages"]] == [mid]
        await asyncio.to_thread(call, "ack", {"id": mid}, config=config)
        assert not (await asyncio.to_thread(call, "inbox", {"name": "durable"}, config=config))["messages"]
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.communicate(), 5)


async def test_background_worker_failure_exits_daemon_and_releases_sockets(tmp_path):
    process, config = await daemon_process(tmp_path, fail=True)
    _, stderr = await asyncio.wait_for(process.communicate(), 5)
    assert process.returncode != 0
    assert b"controlled worker failure" in stderr
    assert not config.socket.exists()
    assert not config.http_socket.exists()


async def test_client_that_never_reads_does_not_hold_a_daemon_connection_forever(bus):
    bus.store.register({"name": "large"})
    for _ in range(25):
        bus.store.send({"to": "agent:large", "type": "event", "payload": {"text": "x" * 60000}})
    peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    peer.setblocking(False)
    try:
        loop = asyncio.get_running_loop()
        await loop.sock_connect(peer, str(bus.config.socket))
        request = {"id": "slow-reader", "method": "history", "params": {"max_bytes": 8388608}}
        await loop.sock_sendall(peer, (json.dumps(request) + "\n").encode())
        async with asyncio.timeout(1):
            while not bus.clients:
                await asyncio.sleep(0.01)
        client_task = next(iter(bus.clients))
        await asyncio.wait_for(asyncio.shield(client_task), 8)
        assert not bus.clients
        assert bus.store.inbox({"name": "large", "observer": True})["total"] == 25
    finally:
        peer.close()


async def test_command_output_limit_and_timeout_reap_the_command():
    with pytest.raises(BusError, match="output exceeds"):
        await command(sys.executable, "-c", "import sys;sys.stdout.write('x'*5000000)")
    with pytest.raises(TimeoutError):
        await command(sys.executable, "-c", "import time;time.sleep(60)", timeout=0.02)
    with pytest.raises(TimeoutError):
        await command(
            sys.executable,
            "-c",
            "import os,time;os.close(1);os.close(2);time.sleep(60)",
            timeout=0.02,
        )


async def test_herdr_refreshes_before_submission_and_serializes_aliases(monkeypatch):
    herdr = Herdr()
    herdr.panes = [{"address": "herdr:w1:p3", "pane_id": "w1:p3", "status": "idle"}]
    entered, release = asyncio.Event(), asyncio.Event()

    async def fresh():
        herdr.panes[0]["status"] = "working"

    monkeypatch.setattr(herdr, "refresh", fresh)
    prompt = AsyncMock()
    monkeypatch.setattr("abus.herdr.command", prompt)
    with pytest.raises(BusError, match="waiting for idle"):
        await herdr.prompt("herdr:w1:p3", {"id": "one"})
    assert not prompt.called

    async def send(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return json.dumps({"result": {"type": "agent_prompted"}})

    herdr.panes[0]["status"] = "idle"
    monkeypatch.setattr(herdr, "refresh", AsyncMock())
    monkeypatch.setattr("abus.herdr.command", send)
    first = asyncio.create_task(herdr.prompt("herdr:w1:p3", {"id": "one"}, "alias-one"))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(BusError, match="pending submission"):
            await herdr.prompt("herdr:w1:p3", {"id": "two"}, "alias-two")
        release.set()
        await first
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.parametrize("mid", ["$(printf ACR)", "--help", "a 'quote' `command` ; literal"])
async def test_herdr_notification_commands_preserve_opaque_ids(monkeypatch, mid):
    herdr = Herdr()
    herdr.panes = [{"address": "herdr:w1:p3", "pane_id": "w1:p3", "status": "idle"}]
    monkeypatch.setattr(herdr, "refresh", AsyncMock())
    prompt = AsyncMock(return_value='{"result":{"type":"agent_prompted"}}')
    monkeypatch.setattr("abus.herdr.command", prompt)
    await herdr.prompt("herdr:w1:p3", {"id": mid}, "worker")
    assert prompt.call_args.args[2] == "prompt-if-idle"
    content = prompt.call_args.args[4]
    ack = next(line for line in content.splitlines() if line.startswith("omarchy abus ack "))
    assert shlex.split(ack) == ["omarchy", "abus", "ack", "--name", "worker", "--", mid]
