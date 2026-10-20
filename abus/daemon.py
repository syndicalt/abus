import asyncio
import contextlib
import fcntl
import json
import logging
import os
import signal
import stat
import time

from aiohttp import web

from . import __version__
from .config import BusError, Config
from .herdr import Herdr
from .network import Network
from .store import Store, address, text

log = logging.getLogger("abus")


class Bus:
    def __init__(self, config):
        self.config = config
        config.prepare()
        self.store = Store(config.state / "abus.db")
        self.herdr = Herdr(os.environ.get("ABUS_HERDR_SESSION"))
        self.network = Network(config, self.store)
        self.changed = asyncio.Event()
        self.started = time.time()
        self.tasks = []
        self.server = None
        self.runner = None
        self.lock = None

    def notify(self):
        previous = self.changed
        self.changed = asyncio.Event()
        previous.set()

    def health(self):
        return {
            "service": "abus",
            "version": __version__,
            "machine": self.network.machine,
            "socket": str(self.config.socket),
            "uptime": int(time.time() - self.started),
            "agents": len(self.store.agents()),
            "messages": self.store.counts(),
            "herdr": {"panes": len(self.herdr.panes), "error": self.herdr.error},
            "network_error": self.network.error,
        }

    async def dispatch(self, method, p):
        if not isinstance(p, dict):
            raise BusError("invalid_params", "params must be an object")
        if method == "register":
            return self.store.register(p)
        if method == "unregister":
            return self.store.unregister(p)
        if method == "heartbeat":
            return self.store.heartbeat(p)
        if method in {"subscribe", "unsubscribe"}:
            return self.store.subscribe(p, remove=method == "unsubscribe")
        if method == "send":
            result = self.store.send(p)
            self.notify()
            return result
        if method in {"inbox", "claim"}:
            wait = p.get("wait", 0)
            if type(wait) not in {int, float} or not 0 <= wait <= 25:
                raise BusError("invalid_params", "wait must be 0..25 seconds")
            # Capture Event before reading so a send cannot be lost between the two.
            deadline = time.monotonic() + wait
            while True:
                event = self.changed
                result = self.store.inbox(p, claim=method == "claim")
                remaining = deadline - time.monotonic()
                if result["messages"] or remaining <= 0:
                    break
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), min(remaining, 1))
            return result
        if method in {"ack", "release", "renew"}:
            return self.store.ack(p, release=method == "release", renew=method == "renew")
        if method == "list":
            return {"agents": self.store.agents(), "herdr": self.herdr.panes, "herdr_error": self.herdr.error}
        if method == "status":
            return {**self.health(), "exposure": await self.network.exposure()}
        if method == "history":
            return self.store.history(p)
        if method == "retry":
            result = self.store.retry(p)
            self.notify()
            return result
        if method == "machines":
            return await self.network.machines()
        if method == "expose":
            enabled = p.get("enabled")
            if type(enabled) is not bool:
                raise BusError("invalid_params", "enabled must be a boolean")
            return await self.network.expose(enabled)
        if method == "dashboard":
            activity = self.store.history({"limit": 100, "before": p.get("activity_before", 0)})
            return {
                "status": self.health(),
                "agent_name": p.get("name"),
                "agents": self.store.agents(),
                "herdr": self.herdr.panes,
                "inbox": self.store.inbox(
                    {
                        "name": p["name"],
                        "observer": True,
                        "include_acked": bool(p.get("include_acked")),
                        "after": p.get("inbox_after", 0),
                    }
                )
                if p.get("name") and self.store.registered(p["name"])
                else {"messages": [], "more": False},
                "messages": activity["messages"],
                "activity": activity,
                "traffic": self.store.traffic(),
                "routes": self.store.routes(),
            }
        raise BusError("unknown_method", f"Unknown method: {method}")

    async def request(self, req):
        rid = req.get("id") if isinstance(req, dict) else None
        try:
            if not isinstance(req, dict):
                raise BusError("invalid_request", "Request must be an object")
            method = text(req.get("method"), "method", 32)
            result = await self.dispatch(method, req.get("params", {}))
            return {"id": rid, "result": result}
        except BusError as e:
            return {"id": rid, "error": e.as_dict()}
        except (ValueError, TypeError, OverflowError) as e:
            return {"id": rid, "error": {"code": "invalid_params", "message": str(e)}}
        except Exception:
            log.exception("Request failed")
            return {
                "id": rid,
                "error": {"code": "internal_error", "message": "Internal error; see the ABUS journal"},
            }

    async def handle(self, reader, writer):
        try:
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), 35)
                    if not line:
                        break
                    req = json.loads(
                        line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
                    )
                    response = await self.request(req)
                except (ValueError, UnicodeError) as e:
                    response = {"id": None, "error": {"code": "parse_error", "message": str(e)[:200]}}
                writer.write((json.dumps(response, allow_nan=False) + "\n").encode())
                await writer.drain()
        except (ConnectionError, TimeoutError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def http_status(self, request):
        return web.json_response({"service": "abus", "version": __version__, "machine": self.network.machine})

    async def http_send(self, request):
        try:
            principal = await self.network.principal(request.headers)
            params = await request.json()
            if not isinstance(params, dict):
                raise BusError("invalid_params", "Expected an object")
            _, machine, _ = address(params.get("to"))
            if machine:
                raise BusError("forbidden", "Remote relaying is not allowed")
            params["from"] = self.network.remote_sender(text(params.get("from", "cli"), "from"), principal)
            result = self.store.send(params, origin=principal["machine"])
            self.notify()
            return web.json_response({"result": result})
        except BusError as e:
            return web.json_response({"error": e.as_dict()}, status=403 if e.code == "forbidden" else 400)
        except (ValueError, TypeError):
            return web.json_response(
                {"error": {"code": "invalid_params", "message": "Invalid JSON payload"}}, status=400
            )

    async def http_list(self, request):
        try:
            await self.network.principal(request.headers)
            machine = self.network.machine
            agents = [
                {
                    "name": a["name"],
                    "address": f"agent:{machine}/{a['name']}",
                    "capabilities": a["capabilities"],
                    "online": a["online"],
                    "unread": a["unread"],
                }
                for a in self.store.agents()
            ]
            panes = [{**p, "address": f"herdr:{machine}/{p['pane_id']}"} for p in self.herdr.panes]
            return web.json_response({"agents": agents, "herdr": panes})
        except BusError as e:
            return web.json_response({"error": e.as_dict()}, status=403)

    async def deliver(self, row):
        envelope = self.store.envelope(row)
        kind, machine, target = address(envelope["to"])
        try:
            if machine:
                await self.network.forward(envelope)
                self.store.delivered(row["id"], "forwarded")
            elif kind == "herdr":
                await self.herdr.prompt(envelope["to"], envelope)
                self.store.delivered(row["id"], "submitted")
            else:
                agent = self.store.registered(target)
                if not agent:
                    raise BusError("unregistered", "Recipient was unregistered")
                if agent["endpoint"]:
                    await self.herdr.prompt(agent["endpoint"], envelope, target)
                    self.store.delivered(row["id"], "notified")
                else:
                    self.store.delivered(row["id"], "accepted")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            permanent = isinstance(e, BusError) and e.code in {
                "invalid_params",
                "unregistered",
                "id_conflict",
                "forbidden",
            }
            self.store.failed(row["id"], str(e) or type(e).__name__, permanent)

    async def maintenance(self):
        while True:
            self.store.prune()
            # Sequential delivery preserves order to each destination; external IO
            # is async, so local clients remain responsive throughout retries.
            for row in self.store.pending():
                await self.deliver(row)
            await asyncio.sleep(0.5)

    async def discovery(self):
        previous = {}
        while True:
            await self.herdr.refresh()
            current = {p["address"]: p for p in self.herdr.panes}
            for target, pane in current.items():
                if previous.get(target) != pane:
                    self.store.send(
                        {"to": "broadcast:status", "from": target, "type": "status", "payload": pane}
                    )
                    self.notify()
            for target in previous.keys() - current.keys():
                self.store.send(
                    {
                        "to": "broadcast:status",
                        "from": target,
                        "type": "status",
                        "payload": {"address": target, "status": "offline"},
                    }
                )
                self.notify()
            previous = current
            await asyncio.sleep(self.config.poll_interval)

    async def start(self):
        self.lock = open(self.config.runtime / "abus.lock", "a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            self.lock.close()
            self.lock = None
            raise RuntimeError("Another ABUS daemon already owns this runtime directory") from e
        for path in (self.config.socket, self.config.http_socket):
            if path.exists():
                if not stat.S_ISSOCK(path.stat().st_mode):
                    raise RuntimeError(f"Refusing to replace non-socket {path}")
                path.unlink()
        await self.network.start()
        self.server = await asyncio.start_unix_server(self.handle, path=str(self.config.socket), limit=262144)
        os.chmod(self.config.socket, 0o600)
        app = web.Application(client_max_size=262144)
        app.router.add_get("/v1/status", self.http_status)
        app.router.add_post("/v1/send", self.http_send)
        app.router.add_get("/v1/list", self.http_list)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.UnixSite(self.runner, str(self.config.http_socket)).start()
        os.chmod(self.config.http_socket, 0o600)
        if self.config.gateway_port is not None:
            await web.TCPSite(self.runner, "127.0.0.1", self.config.gateway_port).start()
        self.tasks = [
            asyncio.create_task(self.maintenance()),
            asyncio.create_task(self.discovery()),
            asyncio.create_task(self.network.refresh()),
        ]
        log.info("ABUS %s listening on %s", __version__, self.config.socket)

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.runner:
            await self.runner.cleanup()
        await self.network.close()
        self.store.close()
        if self.lock:
            for path in (self.config.socket, self.config.http_socket):
                path.unlink(missing_ok=True)
            self.lock.close()


async def run():
    os.umask(0o077)
    bus = Bus(Config.environment())
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    try:
        await bus.start()
        stop_task = asyncio.create_task(stopped.wait())
        try:
            done, _ = await asyncio.wait([stop_task, *bus.tasks[:2]], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not stop_task:
                    await task
                    raise RuntimeError("An ABUS background worker unexpectedly stopped")
        finally:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
    finally:
        await bus.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
