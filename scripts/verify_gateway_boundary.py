#!/usr/bin/env python3
"""Exercise the gateway with a different kernel UID in a private user namespace.

Run: uv run unshare --user --map-auto --map-root-user python scripts/verify_gateway_boundary.py
Requires subordinate UID/GID mappings and unprivileged user namespaces. Creates
only disposable stores and sockets; external adapters are controlled fixtures.
"""

import asyncio
import json
import os
import socket
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from abus.config import Config  # noqa: E402
from abus.daemon import Bus  # noqa: E402


async def foreign_request(family, destination, route, body=None):
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            os.setgid(1)
            os.setuid(1)
            with socket.socket(family) as client:
                client.settimeout(3)
                client.connect(destination)
                payload = json.dumps(body).encode() if body is not None else b""
                method = "POST" if body is not None else "GET"
                client.sendall(
                    (
                        f"{method} /v1/{route} HTTP/1.1\r\nHost: abus\r\n"
                        "X-Forwarded-For: 100.64.0.1\r\nConnection: close\r\n"
                        f"Content-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n"
                    ).encode()
                    + payload
                )
                response = b""
                while chunk := client.recv(4096):
                    response += chunk
                result = {"status": int(response.split(b" ")[1]), "body": response.decode()}
        except PermissionError:
            result = {"denied_by_kernel": True}
        except BaseException as error:
            result = {"error": repr(error)}
        os.write(write_fd, json.dumps(result).encode())
        os._exit(0)
    os.close(write_fd)
    reader = asyncio.StreamReader()
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(read_fd, "rb", buffering=0)
    )
    try:
        raw = await asyncio.wait_for(reader.read(16384), 5)
        _, status = os.waitpid(pid, 0)
        assert status == 0
        return json.loads(raw)
    finally:
        transport.close()


async def verify():
    mappings = Path("/proc/self/uid_map").read_text().splitlines()
    if os.getuid() != 0 or len(mappings) < 2:
        raise RuntimeError("Run inside the documented private user namespace with subordinate UIDs")
    with tempfile.TemporaryDirectory(prefix="abus-uid-") as directory:
        root = Path(directory)
        daemon = Bus(Config(root / "run", root / "state", gateway_port=8744))
        daemon.herdr.refresh = AsyncMock(return_value=[])
        daemon.network.refresh = AsyncMock()
        daemon.network.principal = AsyncMock(return_value={"machine": "fixture.test", "user": "fixture"})
        daemon.store.register({"name": "private-worker"})
        daemon.herdr.panes = [{"pane_id": "private-pane", "title": "private project", "cwd": "/private"}]
        try:
            await daemon.start()
            assert len(daemon.runner.sites) == 1
            # The production directory boundary denies another UID.
            denied = await foreign_request(socket.AF_UNIX, str(daemon.config.http_socket), "list")
            assert denied == {"denied_by_kernel": True}, denied
            # Independently verify socket permissions with traversable parents.
            root.chmod(0o755)
            daemon.config.runtime.chmod(0o755)
            denied = await foreign_request(socket.AF_UNIX, str(daemon.config.http_socket), "list")
            assert denied == {"denied_by_kernel": True}, denied
            # Even if permissions are accidentally relaxed, SO_PEERCRED rejects
            # the foreign UID before consulting WhoIs, listing or storing data.
            daemon.config.http_socket.chmod(0o666)
            site = web.TCPSite(daemon.runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            for family, destination in (
                (socket.AF_UNIX, str(daemon.config.http_socket)),
                (socket.AF_INET, ("127.0.0.1", port)),
            ):
                for route in ("list", "status", "send"):
                    body = {"to": "agent:private-worker", "type": "event"} if route == "send" else None
                    rejected = await foreign_request(family, destination, route, body)
                    assert rejected.get("status") == 403, rejected
                    assert "private project" not in rejected["body"]
            daemon.network.principal.assert_not_awaited()
            assert not daemon.store.inbox({"name": "private-worker", "observer": True})["messages"]
            # The owner still reaches the private gateway using the same header.
            async with aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=str(daemon.config.http_socket))
            ) as client:
                async with client.get("http://abus/v1/list", headers={"X-Forwarded-For": "100.64.0.1"}) as r:
                    assert r.status == 200
            print(
                json.dumps(
                    {
                        "uid_map": mappings,
                        "directory_boundary": "denied",
                        "socket_boundary": "denied",
                        "foreign_uid_unix_and_tcp": "403 before WhoIs; no disclosure or enqueue",
                        "owner_unix": 200,
                        "production_tcp_listeners": 0,
                    },
                    indent=2,
                )
            )
        finally:
            await daemon.close()


if __name__ == "__main__":
    asyncio.run(verify())
