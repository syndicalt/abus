import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web

from abus.config import BusError, Config
from abus.network import Network
from abus.store import Store

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("payload", [{"text": "\ud800"}, {"\udfff": "key"}, {"nested": [float("nan")]}])
def test_invalid_unicode_and_nonfinite_payloads_never_enter_storage(tmp_path, payload):
    store = Store(tmp_path / "bus.db")
    try:
        store.register({"name": "receiver"})
        with pytest.raises(BusError, match="valid Unicode"):
            store.send({"to": "agent:receiver", "type": "event", "payload": payload})
        assert store.history({})["messages"] == []
    finally:
        store.close()


async def test_legacy_surrogates_remain_readable_through_real_cli(bus):
    bus.store.register({"name": "legacy"})
    mid = bus.store.send({"to": "agent:legacy", "type": "event"})["id"]
    with bus.store.db:
        bus.store.db.execute(
            "UPDATE messages SET payload=? WHERE id=?", (json.dumps({"text": "\ud800"}), mid)
        )
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "bin/omarchy-abus"),
        "rpc",
        "dashboard",
        '{"name":"legacy"}',
        env={**os.environ, "XDG_RUNTIME_DIR": str(bus.config.runtime)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(child.communicate(), 5)
    assert child.returncode == 0, stderr
    assert json.loads(stdout)["inbox"]["messages"][0]["payload"]["text"] == "\ud800"


def test_cursor_never_reuses_a_deleted_high_water_mark_after_restart(tmp_path):
    path = tmp_path / "bus.db"
    store = Store(path)
    store.register({"name": "worker"})
    store.send({"to": "agent:worker", "type": "event"})
    cursor = store.inbox({"name": "worker"})["next"]
    with store.db:
        store.db.execute("DELETE FROM messages")
    store.close()
    store = Store(path)
    try:
        mid = store.send({"to": "agent:worker", "type": "event"})["id"]
        page = store.inbox({"name": "worker", "after": cursor})
        assert page["next"] > cursor
        assert [message["id"] for message in page["messages"]] == [mid]
    finally:
        store.close()


def test_reads_do_not_write_and_byte_pagination_keeps_every_message(tmp_path):
    store = Store(tmp_path / "bus.db")
    try:
        store.register({"name": "worker"})
        for _ in range(5):
            store.send({"to": "agent:worker", "type": "event", "payload": {"text": "x" * 60000}})
        writes = store.db.total_changes
        seen, after = [], 0
        while True:
            page = store.inbox({"name": "worker", "observer": True, "after": after, "max_bytes": 131072})
            assert len(page["messages"]) <= 2
            seen.extend(message["id"] for message in page["messages"])
            after = page["next"]
            if not page["more"]:
                break
        assert len(set(seen)) == len(seen) == 5
        assert store.db.total_changes == writes
    finally:
        store.close()


def test_unicode_envelope_budget_counts_escaped_bytes_before_claiming(tmp_path):
    store = Store(tmp_path / "bus.db")
    try:
        store.register({"name": "worker"})
        with pytest.raises(BusError, match="valid Unicode"):
            store.register({"name": "bad", "capabilities": ["\ud800"]})
        mid = store.send({"to": "agent:worker", "type": "event", "payload": {"text": "😀" * 16000}})["id"]
        with pytest.raises(BusError, match="max_bytes must be at least"):
            store.inbox({"name": "worker", "owner": "first", "max_bytes": 131072}, claim=True)
        assert store.db.execute("SELECT lease_until FROM receipts").fetchone()[0] == 0
        page = store.inbox({"name": "worker", "owner": "second", "max_bytes": 262144}, claim=True)
        assert page["messages"][0]["id"] == mid
        assert len(json.dumps(page, separators=(",", ":")).encode("ascii")) < 262144
        assert store.message({"id": mid})["payload"]["text"] == "😀" * 16000
    finally:
        store.close()


def test_long_lease_renewal_preserves_deadline_and_expired_owner_is_fenced(tmp_path):
    store = Store(tmp_path / "bus.db")
    try:
        store.register({"name": "worker"})
        mid = store.send({"to": "agent:worker", "type": "event"})["id"]
        store.inbox({"name": "worker", "owner": "original", "lease": 3600}, claim=True)
        deadline = store.db.execute("SELECT lease_until FROM receipts").fetchone()[0]
        receipt = {"id": mid, "name": "worker", "owner": "original"}
        store.ack(receipt, renew=True)
        assert store.db.execute("SELECT lease_until FROM receipts").fetchone()[0] >= deadline
        with store.db:
            store.db.execute("UPDATE receipts SET lease_until=0")
        for action in ({"renew": True}, {}):
            with pytest.raises(BusError, match="active claim"):
                store.ack(receipt, **action)
        store.inbox({"name": "worker", "owner": "replacement"}, claim=True)
        with pytest.raises(BusError, match="another receiver"):
            store.ack(receipt)
    finally:
        store.close()


@pytest.mark.parametrize("mode", [0o777, 0o755])
def test_shared_runtime_is_refused_without_changing_its_permissions(tmp_path, mode):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=mode)
    runtime.chmod(mode)
    with pytest.raises(RuntimeError, match="private"):
        Config(runtime, tmp_path / "state").prepare()
    assert runtime.stat().st_mode & 0o777 == mode


async def test_unrelated_sends_do_not_wake_another_inbox(bus, monkeypatch):
    for name in ("one", "two"):
        bus.store.register({"name": name})
    observed = []
    inbox = bus.store.inbox

    def read(params, **kwargs):
        observed.append(params["name"])
        return inbox(params, **kwargs)

    monkeypatch.setattr(bus.store, "inbox", read)
    waiting = asyncio.create_task(bus.dispatch("inbox", {"name": "one", "wait": 5}))
    try:
        await asyncio.sleep(0)
        for _ in range(10):
            await bus.dispatch("send", {"to": "agent:two", "type": "event"})
        await asyncio.sleep(0.02)
        assert observed.count("one") == 1
        await bus.dispatch("send", {"to": "agent:one", "type": "event"})
        assert len((await asyncio.wait_for(waiting, 1))["messages"]) == 1
        assert not bus.waiters
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


async def test_external_delivery_is_parallel_but_ordered_per_destination(bus, monkeypatch):
    release = asyncio.Event()
    healthy = asyncio.Event()
    second = asyncio.Event()
    order = []

    async def forward(envelope):
        order.append(envelope["id"])
        if envelope["id"] == "first":
            await release.wait()
        elif envelope["id"] == "second":
            second.set()
        else:
            healthy.set()

    monkeypatch.setattr(bus.network, "forward", forward)
    for mid, destination in [("first", "slow"), ("second", "slow"), ("healthy", "fast")]:
        await bus.dispatch("send", {"id": mid, "to": f"agent:{destination}/worker", "type": "event"})
    await asyncio.wait_for(healthy.wait(), 1)
    assert not second.is_set()
    release.set()
    await asyncio.wait_for(second.wait(), 1)
    assert order.index("first") < order.index("second")


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
async def test_listener_stops_receiver_before_releasing_claim(bus, tmp_path, signum):
    bus.store.register({"name": "worker"})
    mid = bus.store.send({"to": "agent:worker", "type": "event"})["id"]
    pidfile = tmp_path / "receiver.pid"
    program = (
        f"import os,time,pathlib;pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid()));time.sleep(120)"
    )
    listener = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "bin/omarchy-abus"),
        "listen",
        "agent:worker",
        "--exec",
        sys.executable,
        "-c",
        program,
        env={**os.environ, "XDG_RUNTIME_DIR": str(bus.config.runtime)},
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    receiver_pid = None
    try:
        async with asyncio.timeout(3):
            while not pidfile.exists():
                await asyncio.sleep(0.01)
        receiver_pid = int(pidfile.read_text())
        listener.send_signal(signum)
        _, stderr = await asyncio.wait_for(listener.communicate(), 5)
        assert listener.returncode == 128 + signum, stderr
        with pytest.raises(ProcessLookupError):
            os.kill(receiver_pid, 0)
        claimed = bus.store.inbox({"name": "worker", "owner": "replacement"}, claim=True)
        assert claimed["messages"][0]["id"] == mid
    finally:
        if listener.returncode is None:
            listener.kill()
            await listener.wait()
        if receiver_pid:
            try:
                os.killpg(receiver_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.fixture
async def local_api(tmp_path, monkeypatch):
    state = {"config": {}, "etag": '"one"', "writes": [], "conflict": False}

    async def serve(request):
        if request.method == "GET":
            return web.json_response(state["config"], headers={"Etag": state["etag"]})
        if state["conflict"] or request.headers.get("If-Match") != state["etag"]:
            return web.Response(status=412)
        state["config"] = await request.json()
        state["writes"].append(state["config"])
        state["etag"] = '"two"'
        return web.Response()

    app = web.Application()
    app.router.add_route("*", "/localapi/v0/serve-config", serve)
    runner = web.AppRunner(app)
    await runner.setup()
    path = tmp_path / "tailscale.sock"
    await web.UnixSite(runner, str(path)).start()
    network = Network(Config(tmp_path / "run", tmp_path / "state", gateway_port=8744))
    await network.start()
    await network.local.close()
    network.local = aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(path)))
    network.status = {"Self": {"DNSName": "node.test."}}
    network.error = None
    monkeypatch.setattr(network, "refresh", AsyncMock())
    try:
        yield network, state
    finally:
        await network.close()
        await runner.cleanup()


async def test_serve_configuration_updates_are_guarded_and_preserve_other_ports(local_api):
    network, state = local_api
    other = {"node.test:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9000"}}}}
    state["config"] = {"TCP": {"443": {"HTTPS": True}}, "Web": other.copy()}
    assert (await network.expose(True))["enabled"] is True
    assert state["config"]["Web"]["node.test:443"] == other["node.test:443"]
    assert (await network.expose(False))["enabled"] is False
    assert state["config"] == {"TCP": {"443": {"HTTPS": True}}, "Web": other}
    assert len(state["writes"]) == 2


async def test_serve_accepts_null_optional_maps(local_api):
    network, state = local_api
    state["config"] = {"TCP": None, "Web": None}
    assert (await network.expose(True))["enabled"] is True
    assert (await network.expose(False))["enabled"] is False


@pytest.mark.parametrize("proxy", ["http://127.0.0.1:9999", "http://127.0.0.1:87440"])
async def test_serve_guard_rejects_substring_and_other_mount_ownership(local_api, proxy):
    network, state = local_api
    state["config"] = {
        "TCP": {"8743": {"HTTPS": True}},
        "Web": {
            "node.test:8743": {
                "Handlers": {"/": {"Proxy": proxy}, "/abus": {"Proxy": "http://127.0.0.1:8744"}}
            }
        },
    }
    with pytest.raises(BusError, match="another service"):
        await network.expose(True)
    assert not state["writes"]


async def test_serve_does_not_overwrite_a_concurrent_configuration_change(local_api):
    network, state = local_api
    state["conflict"] = True
    with pytest.raises(BusError) as failure:
        await network.expose(True)
    assert failure.value.code == "config_conflict"
    assert state["config"] == {}
    assert not state["writes"]


async def test_unknown_exposure_is_distinct_from_disabled(local_api, monkeypatch):
    network, _ = local_api
    monkeypatch.setattr(
        network, "serve_config", AsyncMock(side_effect=BusError("unavailable", "inspection failed"))
    )
    status = await network.exposure()
    assert status["enabled"] is None
    assert "inspection failed" in status["error"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"result": {"id": "wrong", "state": "accepted", "duplicate": False}},
        {"padding": "x" * 20000},
        {"result": {"id": "outbound", "state": "accepted", "duplicate": "no"}},
        {"result": {"id": "outbound", "state": [], "duplicate": False}},
    ],
)
async def test_invalid_peer_success_keeps_the_durable_outbox_queued(bus, tmp_path, monkeypatch, body):
    async def accept(_request):
        return web.json_response(body)

    app = web.Application()
    app.router.add_post("/v1/send", accept)
    runner = web.AppRunner(app)
    await runner.setup()
    path = tmp_path / "peer.sock"
    await web.UnixSite(runner, str(path)).start()
    await bus.network.session.close()
    bus.network.session = aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(path)))
    monkeypatch.setattr(bus.network, "resolve", AsyncMock(return_value="http://peer"))
    try:
        bus.store.send({"id": "outbound", "to": "agent:peer/worker", "type": "event"})
        await bus.deliver(bus.store.db.execute("SELECT * FROM messages WHERE id='outbound'").fetchone())
        message = bus.store.message({"id": "outbound"})
        assert message["state"] == "queued"
        assert message["last_error"]
    finally:
        await runner.cleanup()


def test_remote_herdr_sender_is_a_routable_return_address():
    assert Network.remote_sender("herdr:w1:p3", {"machine": "source.test"}) == "herdr:source.test/w1:p3"
