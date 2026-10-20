import argparse
import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .client import call
from .config import BusError, Config
from .store import agent_name

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / "ui/shell.qml").is_file():
    ROOT = Path(__file__).resolve().parent / "resources"


def parser():
    p = argparse.ArgumentParser(prog="omarchy abus", description="Durable messaging between Omarchy agents")
    p.add_argument("--pretty", action="store_true", help="Indent JSON output")
    commands = p.add_subparsers(dest="command", required=True)
    r = commands.add_parser("register", help="Create or update a durable agent mailbox")
    r.add_argument("name")
    r.add_argument("--capabilities", nargs="*", default=[])
    r.add_argument("--project")
    r.add_argument("--session")
    r.add_argument("--endpoint", help="Wake this local herdr:<pane> when messages arrive")
    for name in ("unregister", "heartbeat"):
        commands.add_parser(name).add_argument("name")
    r = commands.add_parser("send", help="Send a typed message; - reads a JSON object from stdin")
    r.add_argument("to")
    r.add_argument("type", choices=["prompt", "reply", "status", "event", "request", "ack"])
    r.add_argument("payload", nargs="?", default="{}")
    r.add_argument("--from", dest="sender", default=os.environ.get("ABUS_NAME", "cli"))
    r.add_argument("--corr")
    r.add_argument("--id", help="Stable idempotency key for retries")
    for name in ("inbox", "claim"):
        r = commands.add_parser(name)
        r.add_argument("name", nargs="?", default=os.environ.get("ABUS_NAME"))
        r.add_argument("--limit", type=int, default=100)
        r.add_argument("--after", type=int, default=0)
        r.add_argument("--wait", type=float, default=0)
        r.add_argument("--max-bytes", type=int, default=1024 * 1024)
        if name == "inbox":
            r.add_argument("--observer", action="store_true")
            r.add_argument("--include-acked", action="store_true")
        if name == "claim":
            r.add_argument("--owner", default=str(uuid.uuid4()))
            r.add_argument("--lease", type=int, default=60)
    for name in ("ack", "release", "renew"):
        r = commands.add_parser(name)
        r.add_argument("id")
        r.add_argument("--name", default=os.environ.get("ABUS_NAME"))
        r.add_argument("--owner")
        if name == "renew":
            r.add_argument("--lease", type=int, default=60)
    for name in ("subscribe", "unsubscribe"):
        r = commands.add_parser(name)
        r.add_argument("name")
        r.add_argument("topic", choices=["broadcast:status", "broadcast:events"])
    for name in ("list", "machines", "status", "dashboard"):
        commands.add_parser(name)
    r = commands.add_parser("expose")
    r.add_argument("mode", nargs="?", choices=["on", "off", "status"], default="status")
    r = commands.add_parser("history")
    r.add_argument("--to")
    r.add_argument("--before", type=int, default=0)
    r.add_argument("--limit", type=int, default=100)
    r.add_argument("--max-bytes", type=int, default=1024 * 1024)
    commands.add_parser("message", help="Read one complete retained envelope").add_argument("id")
    commands.add_parser("retry").add_argument("id")
    r = commands.add_parser("listen", help="Feed envelopes to a receiver command and acknowledge on exit 0")
    r.add_argument("name")
    r.add_argument("--once", action="store_true")
    r.add_argument("--exec", dest="handler", nargs=argparse.REMAINDER, required=True)
    commands.add_parser("ui", help="Open the native desktop application")
    commands.add_parser("mcp", help="Run the stdio MCP adapter")
    r = commands.add_parser("rpc", help="Call a local method with JSON parameters")
    r.add_argument("method")
    r.add_argument("params", nargs="?", default="{}")
    return p


def listen(args):
    if not args.handler:
        raise BusError("invalid_params", "--exec requires a receiver command")
    name = agent_name(args.name)
    owner = str(uuid.uuid4())
    starting = False
    stopped = None

    def stop(signum, _frame):
        nonlocal stopped
        stopped = signum
        # Popen may have spawned the child before returning its handle.
        if not starting:
            raise ReceiverStopped(signum)

    signals = (signal.SIGTERM, signal.SIGINT)
    previous = {sig: signal.signal(sig, stop) for sig in signals}
    try:
        while True:
            result = call("claim", {"name": name, "owner": owner, "limit": 1, "wait": 25})
            if not result["messages"]:
                continue
            envelope = result["messages"][0]
            receipt = {"id": envelope["id"], "name": name, "owner": owner}
            child, settled, reaped = None, False, False
            try:
                env = {
                    **os.environ,
                    "ABUS_NAME": f"agent:{name}",
                    "ABUS_MESSAGE_ID": envelope["id"],
                    "ABUS_CORR": envelope["corr"] or envelope["id"],
                }
                starting = True
                try:
                    child = subprocess.Popen(
                        args.handler, stdin=subprocess.PIPE, env=env, start_new_session=True
                    )
                finally:
                    starting = False
                if stopped:
                    raise ReceiverStopped(stopped)
                data = (json.dumps(envelope) + "\n").encode()
                while True:
                    try:
                        child.communicate(input=data, timeout=20)
                        break
                    except subprocess.TimeoutExpired:
                        data = None
                        call("renew", receipt, timeout=5)
                        call("heartbeat", {"name": name}, timeout=5)
                terminate_receiver(child)
                reaped = True
                call("ack" if child.returncode == 0 else "release", receipt, timeout=5)
                settled = True
            finally:
                # A second termination signal must not interrupt reaping or release.
                for sig in signals:
                    signal.signal(sig, signal.SIG_IGN)
                try:
                    if not reaped:
                        terminate_receiver(child)
                    if not settled:
                        try:
                            call("release", receipt, timeout=3)
                        except BusError as error:
                            print(f"Claim release failed; lease will expire: {error}", file=sys.stderr)
                finally:
                    for sig in signals:
                        signal.signal(sig, stop)
            if child.returncode:
                print(f"Receiver exited {child.returncode}; message retained", file=sys.stderr)
            if args.once:
                return child.returncode or 0
            if child.returncode:
                time.sleep(5)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


class ReceiverStopped(BaseException):
    def __init__(self, signum):
        self.signum = signum


def terminate_receiver(child):
    if child is None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # Reap the leader and stop any surviving members of its process group.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()


async def direct_exposure(enabled=None):
    from .network import Network

    network = Network(Config.environment())
    await network.start()
    try:
        return await network.exposure() if enabled is None else await network.expose(enabled)
    finally:
        await network.close()


def execute(args):
    cmd = args.command
    p = {k: v for k, v in vars(args).items() if k not in {"command", "pretty"} and v is not None}
    if cmd == "send":
        p["payload"] = json.loads(sys.stdin.read() if args.payload == "-" else args.payload)
        sender = p.pop("sender")
        if sender != "cli" and not sender.startswith(("agent:", "herdr:")):
            sender = "agent:" + sender
        p["from"] = sender
    if cmd == "expose":
        try:
            if args.mode == "status":
                return call("status")["exposure"]
            return call("expose", {"enabled": args.mode == "on"})
        except BusError as error:
            if error.code != "unavailable" or args.mode == "on":
                raise
            return asyncio.run(direct_exposure(False if args.mode == "off" else None))
    if cmd == "ui":
        path = str(ROOT / "ui/shell.qml")
        existing = subprocess.run(
            ["qs", "ipc", "--path", path, "call", "abus", "open"], capture_output=True, timeout=3
        )
        if existing.returncode == 0:
            if shutil.which("hyprctl"):
                subprocess.run(
                    ["hyprctl", "dispatch", "focuswindow", "class:^(syndicalt.abus)$"],
                    capture_output=True,
                    timeout=3,
                )
            return {"ok": True}
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy/abus"
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (state / "app.log").open("a") as log:
            subprocess.Popen(
                ["qs", "--no-duplicate", "--path", path], start_new_session=True, stdout=log, stderr=log
            )
        return {"ok": True}
    if cmd == "mcp":
        os.execv(sys.executable, [sys.executable, str(ROOT / "mcp/abus_mcp.py")])
    if cmd == "rpc":
        return call(args.method, json.loads(args.params))
    return call(cmd, p)


def main():
    args = parser().parse_args()
    try:
        if args.command == "listen":
            raise SystemExit(listen(args))
        print(
            json.dumps(
                execute(args),
                indent=2 if args.pretty else None,
                separators=None if args.pretty else (",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        )
    except (BusError, ValueError, OSError, subprocess.TimeoutExpired) as e:
        error = e.as_dict() if isinstance(e, BusError) else {"code": "error", "message": str(e)}
        print(json.dumps({"error": error}), file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except ReceiverStopped as error:
        raise SystemExit(128 + error.signum) from None


if __name__ == "__main__":
    main()
