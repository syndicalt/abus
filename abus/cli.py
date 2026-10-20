import argparse
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
from .config import BusError

ROOT = Path(__file__).resolve().parent.parent
if not (ROOT / "ui/shell.qml").is_file():
    ROOT = Path(__file__).resolve().parent / "resources"


def parser():
    p = argparse.ArgumentParser(prog="omarchy abus", description="Durable messaging between Omarchy agents")
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
        if name == "claim":
            r.add_argument("--owner", default=str(uuid.uuid4()))
            r.add_argument("--lease", type=int, default=60)
    for name in ("ack", "release", "renew"):
        r = commands.add_parser(name)
        r.add_argument("id")
        r.add_argument("--name", default=os.environ.get("ABUS_NAME"))
        r.add_argument("--owner")
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
    owner = str(uuid.uuid4())
    while True:
        result = call("claim", {"name": args.name, "owner": owner, "limit": 1, "wait": 25})
        if not result["messages"]:
            continue
        envelope = result["messages"][0]
        receipt = {"id": envelope["id"], "name": args.name, "owner": owner}
        child = None
        try:
            env = {
                **os.environ,
                "ABUS_NAME": f"agent:{args.name}",
                "ABUS_MESSAGE_ID": envelope["id"],
                "ABUS_CORR": envelope["corr"] or envelope["id"],
            }
            child = subprocess.Popen(args.handler, stdin=subprocess.PIPE, env=env, start_new_session=True)
            data = (json.dumps(envelope) + "\n").encode()
            while True:
                try:
                    child.communicate(input=data, timeout=20)
                    break
                except subprocess.TimeoutExpired:
                    data = None
                    call("renew", receipt)
                    call("heartbeat", {"name": args.name})
            if child.returncode == 0:
                call("ack", receipt)
            else:
                call("release", receipt)
                print(f"Receiver exited {child.returncode}; message retained", file=sys.stderr)
                if args.once:
                    return child.returncode or 1
                time.sleep(5)
        except BaseException:
            if child and child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            try:
                call("release", receipt)
            except BusError:
                pass
            raise
        if args.once:
            return 0


def execute(args):
    cmd = args.command
    p = {k: v for k, v in vars(args).items() if k != "command" and v is not None}
    if cmd == "send":
        p["payload"] = json.loads(sys.stdin.read() if args.payload == "-" else args.payload)
        sender = p.pop("sender")
        if sender != "cli" and not sender.startswith(("agent:", "herdr:")):
            sender = "agent:" + sender
        p["from"] = sender
    if cmd == "expose":
        if args.mode == "status":
            return call("status")["exposure"]
        p = {"enabled": args.mode == "on"}
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
        python = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "abus/venv/bin/python"
        if not python.exists():
            python = Path(sys.executable)
        os.execv(str(python), [str(python), str(ROOT / "mcp/abus_mcp.py")])
    if cmd == "rpc":
        return call(args.method, json.loads(args.params))
    return call(cmd, p)


def main():
    args = parser().parse_args()
    try:
        if args.command == "listen":
            raise SystemExit(listen(args))
        print(json.dumps(execute(args), indent=2, ensure_ascii=False, allow_nan=False))
    except (BusError, ValueError, OSError, subprocess.TimeoutExpired) as e:
        error = e.as_dict() if isinstance(e, BusError) else {"code": "error", "message": str(e)}
        print(json.dumps({"error": error}), file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
