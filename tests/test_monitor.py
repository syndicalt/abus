import asyncio
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


async def test_observation_does_not_ack_claim_or_mark_an_agent_online(bus):
    bus.store.register({"name": "worker"})
    mid = bus.store.send({"to": "agent:worker", "type": "request", "payload": {"text": "Inspect me"}})["id"]
    bus.store.inbox({"name": "worker", "owner": "receiver"}, claim=True)
    bus.store.db.execute("UPDATE agents SET seen=0")
    bus.store.db.commit()
    before = dict(bus.store.db.execute("SELECT * FROM receipts WHERE message_id=?", (mid,)).fetchone())
    for _ in range(3):
        dashboard = await bus.dispatch("dashboard", {"name": "worker"})
        assert dashboard["inbox"]["messages"][0]["id"] == mid
        assert dashboard["inbox"]["messages"][0]["claimed"]
        assert not dashboard["agents"][0]["online"]
    after = dict(bus.store.db.execute("SELECT * FROM receipts WHERE message_id=?", (mid,)).fetchone())
    assert before == after
    assert len(dashboard["traffic"]) == 60
    assert sum(dashboard["traffic"]) == 1
    assert dashboard["routes"][0] == {"from": "cli", "to": "agent:worker", "count": 1}


async def test_acknowledged_messages_remain_viewable(bus):
    bus.store.register({"name": "worker"})
    mid = bus.store.send({"to": "agent:worker", "type": "event"})["id"]
    bus.store.ack({"id": mid})
    assert not (await bus.dispatch("dashboard", {"name": "worker"}))["inbox"]["messages"]
    all_messages = (await bus.dispatch("dashboard", {"name": "worker", "include_acked": True}))["inbox"]
    assert all_messages["messages"][0]["acknowledged"]
    assert all_messages["total"] == 1


async def test_monitor_pages_inboxes_without_losing_or_repeating_messages(bus):
    bus.store.register({"name": "paged"})
    for i in range(105):
        bus.store.send({"to": "agent:paged", "type": "event", "id": f"page-{i}"})
    first = await bus.dispatch("dashboard", {"name": "paged"})
    second = await bus.dispatch("dashboard", {"name": "paged", "inbox_after": first["inbox"]["next"]})
    assert len(first["inbox"]["messages"]) == 100
    assert len(second["inbox"]["messages"]) == 5
    assert second["inbox"]["messages"][0]["id"] == "page-100"
    assert not second["inbox"]["more"]


async def process(*argv, env):
    p = await asyncio.create_subprocess_exec(
        *argv, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await asyncio.wait_for(p.communicate(), 4)
    return p.returncode, out, err


@pytest.mark.skipif(not shutil.which("qs"), reason="Quickshell is not installed")
async def test_native_window_loads_and_views_inbox_without_mutation(bus, tmp_path):
    bus.store.register({"name": "monitor-test"})
    mid = bus.store.send(
        {"to": "agent:monitor-test", "type": "request", "payload": {"text": "Read-only test"}}
    )["id"]
    bus.store.db.execute("UPDATE agents SET seen=0")
    bus.store.db.commit()
    runtime = Path(tempfile.mkdtemp(prefix="abus-ui-"))
    (runtime / "abus.sock").symlink_to(bus.config.socket)
    env = {
        **os.environ,
        "XDG_RUNTIME_DIR": str(runtime),
        "QT_QPA_PLATFORM": "offscreen",
        "QS_NO_RELOAD_POPUP": "1",
    }
    log = (tmp_path / "qml.log").open("w+")
    p = await asyncio.create_subprocess_exec(
        "qs", "--path", str(ROOT / "ui/shell.qml"), "--no-color", env=env, stdout=log, stderr=log
    )

    async def ipc(*args):
        return await process(
            "qs", "ipc", "--path", str(ROOT / "ui/shell.qml"), "--any-display", "call", "abus", *args, env=env
        )

    try:
        for _ in range(50):
            if p.returncode is not None:
                log.seek(0)
                pytest.fail(log.read())
            rc, out, _ = await ipc("status")
            if rc == 0 and json.loads(out)["connected"]:
                break
            await asyncio.sleep(0.1)
        else:
            log.seek(0)
            pytest.fail("Native window did not connect: " + log.read())
        assert (await ipc("inbox", "monitor-test"))[0] == 0
        for _ in range(30):
            rc, out, _ = await ipc("status")
            if json.loads(out)["messages"] == 1:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Native inbox did not show the message")
        assert (await ipc("inspect", mid))[0] == 0
        assert json.loads((await ipc("status"))[1])["selectedMessage"] == mid
        assert (
            bus.store.db.execute("SELECT acked FROM receipts WHERE message_id=?", (mid,)).fetchone()[0] == 0
        )
        assert bus.store.registered("monitor-test")["seen"] == 0
        assert (await ipc("overview"))[0] == 0
        assert (await ipc("connections"))[0] == 0
        log.seek(0)
        output = log.read()
        assert not any(
            marker in output
            for marker in ("ERROR:", "ReferenceError", "TypeError", "Cannot assign", "Unable to assign")
        ), output
    finally:
        p.terminate()
        await asyncio.wait_for(p.wait(), 5)
        log.close()
        shutil.rmtree(runtime)


def test_clean_plugin_package_validates(tmp_path):
    if not shutil.which("omarchy-plugin-validate"):
        pytest.skip("Omarchy is not installed")
    target = tmp_path / "plugin"
    shutil.copytree(
        ROOT,
        target,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"),
    )
    result = subprocess.run(["omarchy-plugin-validate", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
