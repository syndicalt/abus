import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from abus import companion
from abus.config import BusError
from abus.herdr import Herdr
from abus.installation import digest


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    content = b"#!/bin/sh\nexit 0\n"
    binary = tmp_path / "herdr"
    binary.write_bytes(content)
    manifest = tmp_path / "integrations/herdr"
    manifest.mkdir(parents=True)
    (manifest / "runtime.json").write_text(
        json.dumps(
            {
                "version": 1,
                "protocol": 22,
                "runtime_version": "0.9.5-abus.test",
                "upstream": "pinned-test-upstream",
                "patch_sha256": "a" * 64,
                "artifacts": {
                    "linux-x86_64": {
                        "url": "https://github.com/syndicalt/abus/releases/download/test/herdr",
                        "sha256": digest(content),
                    }
                },
            }
        )
    )
    (manifest / "LICENSE").write_text("test license")
    monkeypatch.setattr(companion.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(companion.platform, "system", lambda: "Linux")
    return tmp_path, binary


def test_offline_companion_is_verified_and_reused(artifact, monkeypatch):
    root, binary = artifact
    record = companion.prepare(root, artifact=binary)
    assert companion.installed() == record
    assert (companion.namespace() / record["sha256"] / "LICENSE").read_text() == "test license"
    binary.unlink()
    monkeypatch.setattr(companion.urllib.request, "urlopen", lambda *_a, **_kw: pytest.fail("No download"))
    assert companion.prepare(root) == record


def test_bad_artifact_never_becomes_executable(artifact):
    root, binary = artifact
    binary.write_bytes(b"wrong executable")
    with pytest.raises(RuntimeError, match="checksum verification failed"):
        companion.prepare(root, artifact=binary)
    assert not (companion.namespace() / "installation.json").exists()
    assert not list(companion.namespace().glob("*/herdr"))
    assert not list(companion.namespace().glob("*/.download-*"))


def test_installed_binary_tampering_is_detected_before_handoff(artifact):
    root, binary = artifact
    record = companion.prepare(root, artifact=binary)
    from pathlib import Path

    Path(record["binary"]).write_bytes(b"replacement")
    with pytest.raises(RuntimeError, match="integrity verification"):
        companion.installed()


def test_unrelated_runtime_symlink_is_preserved(artifact):
    root, _ = artifact
    companion.namespace().parent.mkdir(parents=True)
    unrelated = root / "unrelated"
    unrelated.mkdir()
    companion.namespace().symlink_to(unrelated, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        companion.prepare(root)
    assert list(unrelated.iterdir()) == []


@pytest.mark.parametrize("capability", [None, False, 1, "true", {}])
def test_missing_or_malformed_runtime_capability_fails_closed(capability):
    assert not Herdr.capable(
        {"type": "pong", "protocol": 22, "capabilities": {"agent_prompt_if_idle": capability}}
    )


async def test_raw_rpc_checks_live_server_and_preserves_literal_payload(tmp_path):
    received = []

    async def handle(reader, writer):
        try:
            request = json.loads(await reader.readline())
            received.append(request)
            response = {"id": request["id"], "result": {"type": "agent_prompted"}}
            writer.write((json.dumps(response) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    socket = str(tmp_path / "herdr.sock")
    server = await asyncio.start_unix_server(handle, path=socket)
    bridge = Herdr()
    bridge.socket = socket
    try:
        result = await bridge.rpc("agent.prompt_if_idle", {"target": "w1:p1", "text": "$(literal); 'x'"})
        assert result["type"] == "agent_prompted"
        assert received[0]["method"] == "agent.prompt_if_idle"
        assert received[0]["params"]["text"] == "$(literal); 'x'"
    finally:
        server.close()
        await server.wait_closed()


async def test_capability_must_come_from_running_server_not_cli_schema(monkeypatch):
    bridge = Herdr()
    monkeypatch.setattr(
        "abus.herdr.command",
        AsyncMock(return_value=json.dumps({"server": {"socket": "/test/herdr.sock"}})),
    )
    rpc = AsyncMock(return_value={"type": "pong", "version": "0.9.1", "protocol": 22, "capabilities": {}})
    monkeypatch.setattr(bridge, "rpc", rpc)
    monkeypatch.setattr(companion, "installed", lambda: (_ for _ in ()).throw(RuntimeError("Not installed")))
    await bridge.ensure_runtime()
    assert not bridge.delivery_ready
    assert "Not installed" in bridge.error
    rpc.assert_awaited_once_with("ping", timeout=3)


async def test_verified_handoff_requires_new_live_capability_and_exact_version(monkeypatch):
    bridge = Herdr()
    monkeypatch.setattr(
        "abus.herdr.command", AsyncMock(return_value='{"server":{"socket":"/test/herdr.sock"}}')
    )
    old = {"type": "pong", "version": "0.9.1", "protocol": 22, "capabilities": {}}
    new = {
        "type": "pong",
        "version": "0.9.5-abus.test",
        "protocol": 22,
        "capabilities": {"agent_prompt_if_idle": True},
    }
    rpc = AsyncMock(side_effect=[old, {"type": "ok"}, new])
    monkeypatch.setattr(bridge, "rpc", rpc)
    monkeypatch.setattr(
        companion,
        "installed",
        lambda: {"binary": "/private/herdr", "protocol": 22, "runtime_version": new["version"]},
    )
    await bridge.ensure_runtime()
    assert bridge.delivery_ready and bridge.error is None
    assert rpc.await_args_list[1].args == (
        "server.live_handoff",
        {"import_exe": "/private/herdr", "expected_protocol": 22, "expected_version": new["version"]},
    )


async def test_unverified_runtime_never_receives_prompt(monkeypatch):
    bridge = Herdr()
    bridge.panes = [{"address": "herdr:w1:p1", "pane_id": "w1:p1", "status": "idle"}]
    monkeypatch.setattr(bridge, "refresh", AsyncMock())
    rpc = AsyncMock()
    monkeypatch.setattr(bridge, "rpc", rpc)
    with pytest.raises(BusError, match="capability has not been verified"):
        await bridge.prompt("herdr:w1:p1", {"id": "one"})
    assert not rpc.called
