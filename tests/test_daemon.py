import asyncio
import json
import os
import stat
from unittest.mock import AsyncMock

import aiohttp
import pytest

from abus.client import call
from abus.config import BusError
from abus.daemon import Bus


async def rpc(bus, method, **params):
    return await asyncio.to_thread(call, method, params, config=bus.config)


async def test_real_socket_permissions_and_committed_messages(bus):
    for path in (bus.config.socket, bus.config.http_socket):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    await rpc(bus, "register", name="durable")
    mid = (await rpc(bus, "send", to="agent:durable", type="prompt", corr="task-1"))["id"]
    # A separate connection sees committed messages while the daemon is running.
    from abus.store import Store

    reopened = Store(bus.config.state / "abus.db")
    assert reopened.inbox({"name": "durable"})["messages"][0]["id"] == mid
    reopened.close()


async def test_malformed_requests_return_errors_and_keep_connection_usable(bus):
    reader, writer = await asyncio.open_unix_connection(bus.config.socket)
    for raw, code in [
        (b"{bad\n", "parse_error"),
        (b"[]\n", "invalid_request"),
        (b'{"id":7,"method":"register","params":{}}\n', "invalid_params"),
    ]:
        writer.write(raw)
        await writer.drain()
        result = json.loads(await reader.readline())
        assert result["error"]["code"] == code
    writer.write(b'{"id":8,"method":"dashboard"}\n')
    await writer.drain()
    assert json.loads(await reader.readline())["id"] == 8
    writer.close()
    await writer.wait_closed()


async def test_second_daemon_cannot_unlink_live_socket(bus):
    with pytest.raises(RuntimeError, match="already owns"):
        Bus(bus.config)
    assert (await rpc(bus, "dashboard"))["status"]["service"] == "abus"


async def test_long_poll_waits_for_its_recipient_and_receives_new_message(bus):
    for name in ("one", "two"):
        await rpc(bus, "register", name=name)
    pending = asyncio.create_task(rpc(bus, "inbox", name="one", wait=2))
    await asyncio.sleep(0.05)
    await rpc(bus, "send", to="agent:two", type="event")
    await asyncio.sleep(0.05)
    assert not pending.done()
    mid = (await rpc(bus, "send", to="agent:one", type="request"))["id"]
    assert (await pending)["messages"][0]["id"] == mid


async def test_large_response_is_framed_and_pageable(bus):
    await rpc(bus, "register", name="large")
    for _i in range(40):
        await rpc(bus, "send", to="agent:large", type="event", payload={"text": "x" * 3000})
    result = await rpc(bus, "inbox", name="large")
    assert len(result["messages"]) == 40
    assert len(json.dumps(result)) > 65536


async def test_herdr_delivery_is_queued_until_idle_and_only_submitted_once(bus, monkeypatch):
    from abus.herdr import Herdr

    bridge = Herdr()
    bridge.panes = [{"address": "herdr:w1:p1", "pane_id": "w1:p1", "status": "working"}]
    bus.herdr = bridge
    monkeypatch.setattr(bridge, "refresh", AsyncMock())
    bridge.delivery_ready = True
    prompt = AsyncMock(return_value={"type": "agent_prompted"})
    monkeypatch.setattr(bridge, "rpc", prompt)
    mid = (
        await rpc(bus, "send", to="herdr:w1:p1", type="prompt", payload={"text": "a 'quote' ; $(literal)"})
    )["id"]
    row = bus.store.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    await bus.deliver(row)
    assert not prompt.called
    assert bus.store.history({})["messages"][0]["state"] == "queued"
    bridge.panes[0]["status"] = "idle"
    await bus.deliver(row)
    assert prompt.call_args.args[0] == "agent.prompt_if_idle"
    assert prompt.call_args.args[1]["target"] == "w1:p1"
    assert "$(literal)" in prompt.call_args.args[1]["text"]
    assert bus.store.db.execute("SELECT state FROM messages WHERE id=?", (mid,)).fetchone()[0] == "submitted"
    assert not bus.store.pending()


async def test_gateway_rejects_anonymous_and_relay_requests(bus, monkeypatch):
    async with aiohttp.ClientSession(
        connector=aiohttp.UnixConnector(path=str(bus.config.http_socket))
    ) as client:
        async with client.post("http://abus/v1/send", json={"to": "agent:target", "type": "event"}) as r:
            assert r.status == 403
        monkeypatch.setattr(
            bus.network, "principal", AsyncMock(return_value={"machine": "sender.test", "user": "owner"})
        )
        async with client.post(
            "http://abus/v1/send", json={"to": "agent:third/target", "type": "event"}
        ) as r:
            assert r.status == 403
        async with client.post("http://abus/v1/register", json={"name": "target"}) as r:
            assert r.status == 404


async def test_remote_delivery_retries_without_duplicating_destination(bus, tmp_path, monkeypatch):
    from abus.config import Config

    destination = Bus(Config(tmp_path / "remote-run", tmp_path / "remote-state"))
    monkeypatch.setattr(destination.herdr, "refresh", AsyncMock(return_value=[]))
    monkeypatch.setattr(destination.network, "refresh", AsyncMock())
    monkeypatch.setattr(
        destination.network, "principal", AsyncMock(return_value={"machine": "sender.test", "user": "owner"})
    )
    await destination.start()
    try:
        destination.store.register({"name": "remote"})
        # Exercise the real HTTP gateway over its private Unix socket.
        await bus.network.session.close()
        bus.network.session = aiohttp.ClientSession(
            connector=aiohttp.UnixConnector(path=str(destination.config.http_socket))
        )
        monkeypatch.setattr(bus.network, "resolve", AsyncMock(return_value="http://remote"))
        mid = (
            await rpc(
                bus,
                "send",
                to="agent:destination/remote",
                type="request",
                corr="task",
                **{"from": "agent:origin"},
            )
        )["id"]
        row = bus.store.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        await bus.deliver(row)
        # Simulate a lost HTTP response followed by retry of the same envelope.
        await bus.network.forward(bus.store.envelope(row))
        messages = destination.store.inbox({"name": "remote"})["messages"]
        assert len(messages) == 1
        assert messages[0]["from"] == "agent:sender.test/origin"
        assert messages[0]["corr"] == "task"
        assert (
            bus.store.db.execute("SELECT state FROM messages WHERE id=?", (mid,)).fetchone()[0] == "forwarded"
        )
    finally:
        await destination.close()


async def test_status_and_unknown_methods(bus):
    assert (await rpc(bus, "status"))["exposure"]["enabled"] is False
    with pytest.raises(BusError, match="Unknown method"):
        await rpc(bus, "nonexistent")
