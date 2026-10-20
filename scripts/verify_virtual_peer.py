#!/usr/bin/env python3
"""Validate a disposable SSH-accessible VM peer on the same tailnet.

The metadata file identifies the VM workspace (path/key and path/known_hosts).
The VM is expected to run /home/abus/abus with /home/abus/venv and the
abus-validation system unit. This script only uses its own temporary mailboxes.
"""

import argparse
import asyncio
import json
import shlex
import subprocess
import time
import uuid
from pathlib import Path

from abus.client import call
from abus.config import Config
from abus.network import Network


def verify(meta):
    workspace = Path(meta["path"])
    ssh = [
        "ssh",
        "-i",
        str(workspace / "key"),
        "-p",
        "22422",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + str(workspace / "known_hosts"),
        "abus@127.0.0.1",
    ]

    def remote(*argv):
        result = subprocess.run(ssh + [shlex.join(argv)], capture_output=True, text=True, timeout=35)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return result.stdout

    def peer_rpc(method, **params):
        raw = remote(
            "env",
            "XDG_RUNTIME_DIR=/run/abus-validation",
            "/home/abus/venv/bin/python",
            "/home/abus/abus/bin/omarchy-abus",
            "rpc",
            method,
            json.dumps(params),
        )
        return json.loads(raw)

    def wait(predicate, description, timeout=65):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.25)
        raise RuntimeError("Timed out: " + description)

    status = json.loads(remote("/home/abus/tailscale/tailscale", "status", "--json"))
    if status["BackendState"] != "Running":
        raise RuntimeError("VM Tailscale node is " + status["BackendState"] + "; authorize it before testing")
    peer = status["Self"]["DNSName"].rstrip(".")
    # Refresh the VM worker's discovery after its first tailnet enrollment.
    remote("sudo", "systemctl", "restart", "abus-validation")
    time.sleep(1)
    local_status = call("status")
    host = local_status["machine"]
    local_exposed = local_status["exposure"]["enabled"]
    peer_exposed = peer_rpc("status")["exposure"]["enabled"]
    suffix = uuid.uuid4().hex[:8]
    local_name, peer_name = "vm-sender-" + suffix, "vm-receiver-" + suffix
    address = f"agent:{peer}/{peer_name}"
    stopped = False
    try:
        call("register", {"name": local_name, "capabilities": ["verification"]})
        peer_rpc("register", name=peer_name, capabilities=["verification"])
        call("expose", {"enabled": True})
        peer_rpc("expose", enabled=True)
        wait(
            lambda: any(
                m["dns"] == peer and any(a["name"] == peer_name for a in m.get("agents", []))
                for m in call("machines")["machines"]
            ),
            "VM and remote recipient discovery",
        )
        print("VM discovery and remote recipient listing: PASS", flush=True)

        params = {
            "to": address,
            "from": f"agent:{local_name}",
            "type": "request",
            "corr": "vm-" + suffix,
            "id": str(uuid.uuid4()),
            "payload": {"text": "Cross-VM request"},
        }
        call("send", params)

        def forwarded(mid):
            matches = [m for m in call("history", {"to": address})["messages"] if m["id"] == mid]
            return matches and matches[0]["state"] == "forwarded"

        wait(lambda: forwarded(params["id"]), "delivery to VM")

        async def lost_response_retry():
            network = Network(Config.environment(), None)
            await network.start()
            try:
                response = await network.forward(params)
                assert response["result"]["duplicate"]
            finally:
                await network.close()

        asyncio.run(lost_response_retry())
        inbox = peer_rpc("inbox", name=peer_name)["messages"]
        assert len(inbox) == 1 and inbox[0]["id"] == params["id"]
        assert inbox[0]["from"] == f"agent:{host}/{local_name}"
        peer_rpc(
            "send",
            to=inbox[0]["from"],
            type="reply",
            payload={"text": "Cross-VM reply"},
            corr=inbox[0]["corr"],
            **{"from": f"agent:{peer_name}"},
        )
        peer_rpc("ack", id=params["id"], name=peer_name)
        reply = wait(lambda: call("inbox", {"name": local_name})["messages"], "reply from VM")[0]
        assert reply["corr"] == params["corr"]
        assert reply["from"] == f"agent:{peer}/{peer_name}"
        call("ack", {"id": reply["id"], "name": local_name})
        print(
            "Real tailnet delivery, node identity, deduplication, correlated reply, and acknowledgments: PASS",
            flush=True,
        )

        remote("sudo", "systemctl", "stop", "abus-validation")
        stopped = True
        offline = {**params, "id": str(uuid.uuid4()), "payload": {"text": "Retain while peer is offline"}}
        call("send", offline)

        def queued_error():
            return any(
                m["id"] == offline["id"] and m["state"] == "queued" and m["last_error"]
                for m in call("history", {"to": address})["messages"]
            )

        wait(queued_error, "visible queued error while VM service is offline", timeout=15)
        remote("sudo", "systemctl", "start", "abus-validation")
        stopped = False
        wait(lambda: forwarded(offline["id"]), "automatic delivery after VM recovery")
        inbox = peer_rpc("inbox", name=peer_name)["messages"]
        assert len(inbox) == 1 and inbox[0]["id"] == offline["id"]
        peer_rpc("ack", id=offline["id"], name=peer_name)
        print("Offline queue, backoff, VM service recovery, and automatic retry: PASS", flush=True)
    finally:
        if stopped:
            try:
                remote("sudo", "systemctl", "start", "abus-validation")
            except Exception as error:
                print("VM service recovery needs attention:", error, flush=True)
        for cleanup in (
            lambda: call("unregister", {"name": local_name}),
            lambda: peer_rpc("unregister", name=peer_name),
            lambda: None if local_exposed else call("expose", {"enabled": False}),
            lambda: None if peer_exposed else peer_rpc("expose", enabled=False),
        ):
            try:
                cleanup()
            except Exception as error:
                print("Cleanup needs attention:", error, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meta", type=Path, required=True)
    verify(json.loads(parser.parse_args().meta.read_text()))
