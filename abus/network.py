"""Tailnet discovery, validated forwarding and conditional Serve configuration."""

import asyncio
import copy
import ipaddress
import socket
import time

import aiohttp

from .config import BusError
from .herdr import command
from .protocol import loads, read_json
from .store import address

LOCAL_API = "http://local-tailscaled.sock/localapi/v0"
ACCEPTED_STATES = {"accepted", "queued", "notified", "submitted", "forwarded", "acked", "failed"}


def config_map(value, field):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise BusError("protocol_error", f"Tailscale {field} must be an object")
    return value


def serve_handler(config, port, expected):
    """Find the owned root mount; never infer ownership from another path or URL."""
    suffix = f":{port}"
    for foreground in config_map(config.get("Foreground"), "Foreground").values():
        foreground = config_map(foreground, "foreground configuration")
        if str(port) in config_map(foreground.get("TCP"), "foreground TCP") or any(
            host.endswith(suffix) for host in config_map(foreground.get("Web"), "foreground Web")
        ):
            raise BusError("port_in_use", f"Tailscale port {port} belongs to a foreground service")
    entries = [
        (host, value) for host, value in config_map(config.get("Web"), "Web").items() if host.endswith(suffix)
    ]
    tcp = config_map(config.get("TCP"), "TCP").get(str(port))
    if not entries:
        if tcp:
            raise BusError("port_in_use", f"Tailscale port {port} belongs to another service")
        return None
    if len(entries) != 1:
        raise BusError("port_in_use", f"Tailscale port {port} has ambiguous host ownership")
    host, web = entries[0]
    handlers = config_map(config_map(web, "Web entry").get("Handlers"), "Handlers")
    if handlers.get("/") != {"Proxy": expected} or tcp != {"HTTPS": True}:
        raise BusError("port_in_use", f"Tailscale port {port} belongs to another service")
    return host


class Network:
    def __init__(self, config, store=None):
        self.config = config
        self.store = store
        self.status = {}
        self.error = "Tailscale has not been inspected"
        self.updated = 0.0
        self.last_refresh = None
        self.session = None
        self.local = None
        self._refresh_lock = asyncio.Lock()
        self._discovery_lock = asyncio.Lock()
        self._exposure_lock = asyncio.Lock()
        self._machines = None
        self._machines_updated = 0.0

    async def start(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=4),
            connector=aiohttp.TCPConnector(limit=self.config.delivery_concurrency + 8),
            auto_decompress=False,
            headers={"Accept-Encoding": "identity"},
        )
        self.local = aiohttp.ClientSession(
            connector=aiohttp.UnixConnector(path=self.config.tailscale_socket, limit=8),
            timeout=aiohttp.ClientTimeout(total=4),
        )

    async def close(self):
        for session in (self.session, self.local):
            if session:
                await session.close()

    @property
    def machine(self):
        node = self.status.get("Self") or {}
        return node.get("DNSName", "").rstrip(".") or socket.gethostname()

    async def monitor(self):
        while True:
            await self.refresh()
            await asyncio.sleep(15)

    async def refresh(self):
        async with self._refresh_lock:
            if self.updated and time.monotonic() - self.updated < 15:
                return
            try:
                status = loads(await command("tailscale", "status", "--json", timeout=4))
                if not isinstance(status, dict) or not isinstance(status.get("Self", {}), dict):
                    raise ValueError("Invalid Tailscale status")
                if not isinstance(status.get("Peer", {}), dict):
                    raise ValueError("Invalid Tailscale peer list")
                if not isinstance(status.get("Self", {}).get("DNSName", ""), str):
                    raise ValueError("Invalid Tailscale node name")
                self.status = status
                self.error = None if status.get("BackendState") == "Running" else "Tailscale is not connected"
            except (BusError, ValueError, TimeoutError) as error:
                self.status = {}
                self.error = str(error) or "Tailscale timed out"
            self.updated = time.monotonic()
            self.last_refresh = time.time()

    def peers(self):
        peers = []
        for node in (self.status.get("Peer") or {}).values():
            if not isinstance(node, dict):
                continue
            name = node.get("DNSName", "")
            if not isinstance(name, str) or any(
                not isinstance(node.get(field), (str, type(None))) for field in ("ID", "HostName")
            ):
                continue
            dns = name.rstrip(".")
            if dns:
                peers.append(
                    {
                        "id": node.get("ID"),
                        "name": node.get("HostName"),
                        "dns": dns,
                        "online": node.get("Online", False),
                        "url": f"https://{dns}:{self.config.network_port}",
                    }
                )
        return peers

    async def _probe(self, peer):
        result = None
        try:
            async with self.session.get(
                peer["url"] + "/v1/status", allow_redirects=False, timeout=aiohttp.ClientTimeout(total=1.5)
            ) as response:
                status = await read_json(response, 16384)
                if response.status != 200 or status.get("service") != "abus":
                    return None
            result = {**peer, "abus": status}
            async with self.session.get(
                peer["url"] + "/v1/list", allow_redirects=False, timeout=aiohttp.ClientTimeout(total=1.5)
            ) as response:
                if response.status != 200:
                    raise BusError("unavailable", f"Discovery returned HTTP {response.status}")
                listing = await read_json(response, 1024 * 1024)
                if not isinstance(listing.get("agents"), list) or not isinstance(listing.get("herdr"), list):
                    raise BusError("protocol_error", "Invalid peer recipients")
                result.update(agents=listing["agents"], herdr=listing["herdr"])
            return result
        except (aiohttp.ClientError, BusError, ValueError, TimeoutError) as error:
            if result is None:
                return None  # An online tailnet device need not run ABUS.
            return {**result, "agents": [], "herdr": [], "error": str(error) or "Peer discovery timed out"}

    async def machines(self):
        async with self._discovery_lock:
            await self.refresh()
            if self._machines is not None and time.monotonic() - self._machines_updated < 15:
                return self._machines
            pending = iter(peer for peer in self.peers() if peer["online"])
            completed = []

            async def worker():
                for peer in pending:
                    completed.append(await self._probe(peer))

            workers = [asyncio.create_task(worker()) for _ in range(8)]
            partial = False
            try:
                await asyncio.wait_for(asyncio.gather(*workers), 20)
            except TimeoutError:
                partial = True
            finally:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
            self._machines = {
                "machines": sorted(
                    [peer for peer in completed if peer and "abus" in peer], key=lambda p: p["dns"]
                ),
                "errors": [peer for peer in completed if peer and "error" in peer],
                "error": self.error
                or ("Discovery deadline reached; results are partial" if partial else None),
                "partial": partial,
                "self": self.machine,
                "updated_at": time.time(),
            }
            self._machines_updated = time.monotonic()
            return self._machines

    async def resolve(self, machine):
        await self.refresh()
        matches = [
            peer
            for peer in self.peers()
            if machine in {peer["id"], peer["name"], peer["dns"], peer["dns"].split(".")[0]}
        ]
        if len(matches) != 1:
            raise BusError("unavailable", f"No unique Tailscale peer named {machine}")
        return matches[0]["url"]

    async def forward(self, envelope):
        kind, machine, target = address(envelope["to"])
        base = await self.resolve(machine)
        params = {key: envelope[key] for key in ("id", "from", "type", "corr", "payload")}
        params["to"] = f"{kind}:{target}"
        async with self.session.post(base + "/v1/send", json=params, allow_redirects=False) as response:
            data = await read_json(response, 8192)
            if response.status != 200 or "error" in data:
                error = data.get("error")
                if not isinstance(error, dict):
                    raise BusError("remote_error", f"Remote HTTP {response.status}")
                raise BusError(
                    str(error.get("code", "remote_error")), str(error.get("message", "Remote error"))
                )
        accepted = data.get("result")
        if (
            not isinstance(accepted, dict)
            or accepted.get("id") != envelope["id"]
            or not isinstance(accepted.get("state"), str)
            or accepted.get("state") not in ACCEPTED_STATES
            or type(accepted.get("duplicate")) is not bool
        ):
            raise BusError("protocol_error", "Peer did not confirm durable acceptance of this message")
        return data

    async def serve_config(self):
        async with self.local.get(LOCAL_API + "/serve-config") as response:
            if response.status != 200:
                raise BusError("unavailable", f"Cannot inspect Tailscale Serve: HTTP {response.status}")
            return await read_json(response, 1024 * 1024, nullable=True), response.headers.get("Etag")

    async def expose(self, enabled):
        async with self._exposure_lock:
            if enabled:
                await self.refresh()
                if self.error or self.config.gateway_port is None:
                    raise BusError("unavailable", self.error or "HTTP gateway is not configured")
            config, etag = await self.serve_config()
            if not etag:
                raise BusError("unavailable", "Tailscale Serve does not provide a configuration guard")
            port = str(self.config.network_port)
            expected = f"http://127.0.0.1:{self.config.gateway_port}"
            owned = serve_handler(config, port, expected)
            if bool(owned) == enabled:
                return await self.exposure()
            changed = copy.deepcopy(config)
            if enabled:
                host = f"{self.machine}:{port}"
                changed["TCP"] = config_map(changed.get("TCP"), "TCP")
                changed["Web"] = config_map(changed.get("Web"), "Web")
                changed["TCP"][port] = {"HTTPS": True}
                changed["Web"][host] = {"Handlers": {"/": {"Proxy": expected}}}
            else:
                handlers = changed["Web"][owned]["Handlers"]
                del handlers["/"]
                if not handlers:
                    del changed["Web"][owned]
                    del changed["TCP"][port]
            async with self.local.post(
                LOCAL_API + "/serve-config", json=changed, headers={"If-Match": etag}
            ) as response:
                if response.status == 412:
                    raise BusError("config_conflict", "Tailscale Serve changed; inspect it and retry")
                if response.status != 200:
                    raise BusError("external_error", f"Cannot update Tailscale Serve: HTTP {response.status}")
            actual = await self.exposure()
            if actual["enabled"] is not enabled:
                raise BusError("unavailable", "Cannot verify the requested Tailscale exposure state")
            self._machines = None
            return actual

    async def exposure(self):
        try:
            config, _ = await self.serve_config()
            expected = f"http://127.0.0.1:{self.config.gateway_port}"
            active = bool(serve_handler(config, self.config.network_port, expected))
            return {
                "enabled": active,
                "url": f"https://{self.machine}:{self.config.network_port}" if active else None,
                "port": self.config.network_port,
                "error": None,
                "updated_at": time.time(),
            }
        except (aiohttp.ClientError, BusError, ValueError, TimeoutError) as error:
            return {
                "enabled": None,
                "url": None,
                "port": self.config.network_port,
                "error": str(error) or "Tailscale inspection timed out",
                "updated_at": time.time(),
            }

    async def principal(self, headers):
        source = headers.get("X-Forwarded-For", "")
        try:
            ip = str(ipaddress.ip_address(source.strip()))
        except ValueError as error:
            raise BusError("forbidden", "A single Tailscale Serve source address is required") from error
        try:
            async with self.local.get(LOCAL_API + "/whois", params={"addr": ip}) as response:
                if response.status != 200:
                    raise BusError("forbidden", "Cannot establish tailnet node identity")
                who = await read_json(response, 65536)
        except (aiohttp.ClientError, ValueError, TimeoutError, BusError) as error:
            raise BusError("forbidden", "Cannot establish tailnet node identity") from error
        node = who.get("Node")
        if not isinstance(node, dict) or not isinstance(node.get("Name"), str) or not node["Name"]:
            raise BusError("forbidden", "Unknown tailnet source")
        return {
            "machine": node["Name"].rstrip("."),
            "user": config_map(who.get("UserProfile"), "UserProfile").get("LoginName") or "tagged-node",
        }

    @staticmethod
    def remote_sender(sender, principal):
        machine = principal["machine"]
        if sender.startswith(("agent:", "herdr:")):
            kind, _, name = address(sender)
            return f"{kind}:{machine}/{name}"
        return f"remote:{machine}/{sender}"[:256]
