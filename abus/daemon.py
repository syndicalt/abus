import asyncio
import contextlib
import fcntl
import json
import logging
import os
import signal
import socket
import stat
import struct
import time

from aiohttp import ClientError, web

from . import __version__
from .config import BusError, Config
from .herdr import Herdr
from .network import Network
from .protocol import loads
from .store import SWEEP_BATCH, Store, address, agent_name, text

log = logging.getLogger("abus")


def private_gateway_peer(transport):
    """Trust forwarded identity only over owner/root Unix connections on Linux."""
    peer = transport.get_extra_info("socket") if transport else None
    try:
        if peer is None or peer.family != socket.AF_UNIX:
            return False
        _, uid, _ = struct.unpack("iII", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    except (AttributeError, OSError, ValueError, struct.error):
        return False
    return uid in {0, os.getuid()}


class Bus:
    def __init__(self, config):
        self.config = config
        config.prepare()
        descriptor = os.open(config.runtime / "abus.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(descriptor, "a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self.lock.close()
            raise RuntimeError("Another ABUS daemon already owns this runtime directory") from error
        try:
            self.store = Store(config.state / "abus.db")
        except BaseException:
            self.lock.close()
            raise
        self.herdr = Herdr(os.environ.get("ABUS_HERDR_SESSION"))
        self.network = Network(config, self.store)
        self.waiters = {}
        self.delivery_changed = asyncio.Event()
        self.started = time.time()
        self.tasks = []
        self.server = None
        self.runner = None
        self.clients = {}
        self.gateway_clients = 0

    def notify(self, mid=None, *, name=None):
        self.delivery_changed.set()
        names = [agent_name(name)] if name else self.store.consumers(mid) if mid else list(self.waiters)
        for recipient in names:
            for event in self.waiters.get(recipient, ()):
                event.set()

    def health(self, agents=None):
        return {
            "service": "abus",
            "version": __version__,
            "machine": self.network.machine,
            "socket": str(self.config.socket),
            "uptime": int(time.time() - self.started),
            "agents": len(self.store.agents() if agents is None else agents),
            "messages": self.store.counts(),
            "herdr": {
                "panes": len(self.herdr.panes),
                "error": self.herdr.error,
                "delivery_ready": self.herdr.delivery_ready,
                "runtime_version": self.herdr.runtime_version,
            },
            "network_error": self.network.error,
            "network_updated_at": self.network.last_refresh,
            "clients": len(self.clients),
        }

    async def dispatch(self, method, p):
        if not isinstance(p, dict):
            raise BusError("invalid_params", "params must be an object")
        if method == "register":
            result = self.store.register(p)
            self.notify(name=result["name"])
            return result
        if method == "unregister":
            result = self.store.unregister(p)
            self.notify(name=p.get("name"))
            return result
        if method == "heartbeat":
            return self.store.heartbeat(p)
        if method in {"subscribe", "unsubscribe"}:
            return self.store.subscribe(p, remove=method == "unsubscribe")
        if method == "send":
            result = self.store.send(p)
            self.notify(result["id"])
            return result
        if method in {"inbox", "claim"}:
            wait = p.get("wait", 0)
            if type(wait) not in {int, float} or not 0 <= wait <= 25:
                raise BusError("invalid_params", "wait must be 0..25 seconds")
            if not wait:
                return self.store.inbox(p, claim=method == "claim")
            name = agent_name(p.get("name"))
            event = asyncio.Event()
            self.waiters.setdefault(name, set()).add(event)
            deadline = time.monotonic() + wait
            try:
                while True:
                    # Clear before reading; Store never yields between the query and this wait.
                    event.clear()
                    result = self.store.inbox(p, claim=method == "claim")
                    remaining = deadline - time.monotonic()
                    if result["messages"] or remaining <= 0:
                        return result
                    lease_delay = self.store.claim_delay(name)
                    timeout = min(remaining, lease_delay) if lease_delay is not None else remaining
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(event.wait(), timeout)
            finally:
                self.waiters[name].remove(event)
                if not self.waiters[name]:
                    del self.waiters[name]
        if method in {"ack", "release", "renew"}:
            result = self.store.ack(p, release=method == "release", renew=method == "renew")
            self.notify(p.get("id"), name=p.get("name"))
            return result
        if method == "list":
            return {"agents": self.store.agents(), "herdr": self.herdr.panes, "herdr_error": self.herdr.error}
        if method == "status":
            return {**self.health(), "exposure": await self.network.exposure()}
        if method == "history":
            return self.store.history(p)
        if method == "message":
            return self.store.message(p)
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
            agents = self.store.agents()
            activity = self.store.history({"limit": 100, "before": p.get("activity_before", 0)})
            return {
                "status": self.health(agents),
                "agent_name": p.get("name"),
                "agents": agents,
                "herdr": self.herdr.panes,
                "inbox": self.store.inbox(
                    {
                        "name": p["name"],
                        "observer": True,
                        "include_acked": p.get("include_acked", False),
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
            if rid is not None and type(rid) not in (str, int):
                rid = None
                raise BusError("invalid_request", "id must be a string, integer, or null")
            if type(rid) is int and not -(2**63) <= rid < 2**63:
                rid = None
                raise BusError("invalid_request", "integer id must fit in 64 bits")
            if isinstance(rid, str):
                text(rid, "id", 128)
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
        task = asyncio.current_task()
        try:
            if len(self.clients) >= 128:
                writer.write(b'{"id":null,"error":{"code":"busy","message":"Too many local clients"}}\n')
                await asyncio.wait_for(writer.drain(), 2)
                return
            self.clients[task] = writer
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), 35)
                    if not line:
                        break
                    req = loads(line)
                    response = await self.request(req)
                except (ValueError, UnicodeError, RecursionError) as e:
                    response = {"id": None, "error": {"code": "parse_error", "message": str(e)[:200]}}
                writer.write((json.dumps(response, allow_nan=False, separators=(",", ":")) + "\n").encode())
                await asyncio.wait_for(writer.drain(), 5)
        except (ConnectionError, TimeoutError):
            pass
        finally:
            self.clients.pop(task, None)
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (ConnectionError, TimeoutError):
                writer.transport.abort()

    async def http_status(self, request):
        return web.json_response({"service": "abus", "version": __version__, "machine": self.network.machine})

    @web.middleware
    async def gateway_limit(self, request, handler):
        if not private_gateway_peer(request.transport):
            return web.json_response(
                {"error": {"code": "forbidden", "message": "An owner-only Unix connection is required"}},
                status=403,
            )
        if self.gateway_clients >= 32:
            return web.json_response({"error": {"code": "busy", "message": "Gateway is busy"}}, status=503)
        self.gateway_clients += 1
        try:
            return await handler(request)
        finally:
            self.gateway_clients -= 1

    async def http_send(self, request):
        try:
            principal = await self.network.principal(request.headers)
            params = await asyncio.wait_for(request.json(loads=loads), 5)
            if not isinstance(params, dict):
                raise BusError("invalid_params", "Expected an object")
            _, machine, _ = address(params.get("to"))
            if machine:
                raise BusError("forbidden", "Remote relaying is not allowed")
            params["from"] = self.network.remote_sender(text(params.get("from", "cli"), "from"), principal)
            result = self.store.send(params, origin=principal["machine"])
            self.notify(result["id"])
            return web.json_response({"result": result})
        except BusError as e:
            return web.json_response({"error": e.as_dict()}, status=403 if e.code == "forbidden" else 400)
        except (ValueError, TypeError, RecursionError, TimeoutError):
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
        except (BusError, ClientError, TimeoutError) as e:
            permanent = isinstance(e, BusError) and e.code in {
                "invalid_params",
                "id_conflict",
                "forbidden",
            }
            self.store.failed(row["id"], str(e) or type(e).__name__, permanent)
            log.warning("Delivery %s to %s: %s", row["id"], envelope["to"], str(e) or type(e).__name__)

    async def maintenance(self):
        active = {}
        next_sweep = 0.0
        wake = None
        try:
            while True:
                for task in list(active):
                    if task.done():
                        del active[task]
                        await task
                self.delivery_changed.clear()
                if time.monotonic() >= next_sweep:
                    removed = self.store.sweep()
                    next_sweep = time.monotonic() + (0 if removed == SWEEP_BATCH else 60)
                slots = self.config.delivery_concurrency - len(active)
                if slots:
                    for row in self.store.pending(limit=slots, exclude=tuple(active.values())):
                        active[asyncio.create_task(self.deliver(row))] = row["to_agent"]
                wake = asyncio.create_task(self.delivery_changed.wait())
                timeout = 0 if next_sweep <= time.monotonic() else 0.5
                await asyncio.wait([wake, *active], timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                wake.cancel()
                await asyncio.gather(wake, return_exceptions=True)
        finally:
            for task in [*active, *([wake] if wake else [])]:
                task.cancel()
            await asyncio.gather(*active, *([wake] if wake else []), return_exceptions=True)

    async def discovery(self):
        previous = {}
        while True:
            await self.herdr.refresh()
            current = {p["address"]: p for p in self.herdr.panes}
            for target, pane in current.items():
                if previous.get(target) != pane:
                    sent = self.store.send(
                        {"to": "broadcast:status", "from": target, "type": "status", "payload": pane}
                    )
                    self.notify(sent["id"])
            for target in previous.keys() - current.keys():
                sent = self.store.send(
                    {
                        "to": "broadcast:status",
                        "from": target,
                        "type": "status",
                        "payload": {"address": target, "status": "offline"},
                    }
                )
                self.notify(sent["id"])
            previous = current
            await asyncio.sleep(self.config.poll_interval)

    async def start(self):
        for path in (self.config.socket, self.config.http_socket):
            if path.exists():
                if not stat.S_ISSOCK(path.stat().st_mode):
                    raise RuntimeError(f"Refusing to replace non-socket {path}")
                path.unlink()
        await self.network.start()
        self.server = await asyncio.start_unix_server(self.handle, path=str(self.config.socket), limit=262144)
        os.chmod(self.config.socket, 0o600)
        app = web.Application(client_max_size=262144, middlewares=[self.gateway_limit])
        app.router.add_get("/v1/status", self.http_status)
        app.router.add_post("/v1/send", self.http_send)
        app.router.add_get("/v1/list", self.http_list)
        self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=5)
        await self.runner.setup()
        await web.UnixSite(self.runner, str(self.config.http_socket)).start()
        os.chmod(self.config.http_socket, 0o600)
        self.tasks = [
            asyncio.create_task(self.maintenance()),
            asyncio.create_task(self.discovery()),
            asyncio.create_task(self.network.monitor()),
        ]
        log.info("ABUS %s listening on %s", __version__, self.config.socket)

    async def close(self):
        for task, writer in list(self.clients.items()):
            writer.close()
            task.cancel()
        await asyncio.gather(*list(self.clients), return_exceptions=True)
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
            self.lock = None


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
            done, _ = await asyncio.wait([stop_task, *bus.tasks], return_when=asyncio.FIRST_COMPLETED)
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
