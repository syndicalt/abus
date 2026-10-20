"""Tailnet discovery and HTTP delivery. Only the private gateway accepts remote sends."""

import asyncio
import json
import socket
import time

import aiohttp

from .config import BusError
from .herdr import command
from .store import address


class Network:
    def __init__(self, config, store):
        self.config = config
        self.store = store
        self.status = {}
        self.error = None
        self.updated = 0
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4))

    async def close(self):
        if self.session:
            await self.session.close()

    @property
    def machine(self):
        s = self.status.get("Self", {})
        return s.get("DNSName", "").rstrip(".") or socket.gethostname()

    async def refresh(self):
        if time.monotonic() - self.updated < 15:
            return
        try:
            self.status = json.loads(await command("tailscale", "status", "--json", timeout=4))
            self.error = (
                None if self.status.get("BackendState") == "Running" else "Tailscale is not connected"
            )
        except (BusError, ValueError, TimeoutError) as e:
            self.status = {}
            self.error = str(e) or "Tailscale timed out"
        self.updated = time.monotonic()

    def peers(self):
        result = []
        for p in self.status.get("Peer", {}).values():
            dns = p.get("DNSName", "").rstrip(".")
            if not dns:
                continue
            result.append(
                {
                    "id": p.get("ID"),
                    "name": p.get("HostName"),
                    "dns": dns,
                    "online": p.get("Online", False),
                    "url": f"https://{dns}:{self.config.network_port}",
                }
            )
        return result

    async def machines(self):
        await self.refresh()
        sem = asyncio.Semaphore(8)

        async def probe(peer):
            async with sem:
                if not peer["online"]:
                    return None
                try:
                    async with self.session.get(
                        peer["url"] + "/v1/status",
                        timeout=aiohttp.ClientTimeout(total=1.5),
                        allow_redirects=False,
                    ) as r:
                        data = await r.json()
                        if r.status == 200 and data.get("service") == "abus":
                            result = {**peer, "abus": data}
                        else:
                            return None
                    async with self.session.get(
                        peer["url"] + "/v1/list",
                        timeout=aiohttp.ClientTimeout(total=1.5),
                        allow_redirects=False,
                    ) as r:
                        if r.status == 200:
                            result.update(await r.json())
                    return result
                except (aiohttp.ClientError, ValueError, TimeoutError):
                    pass
                return None

        peers = await asyncio.gather(*(probe(p) for p in self.peers()))
        return {"machines": [p for p in peers if p], "error": self.error, "self": self.machine}

    async def resolve(self, machine):
        await self.refresh()
        for p in self.peers():
            if machine in {p["id"], p["name"], p["dns"], p["dns"].split(".")[0]}:
                return p["url"]
        raise BusError("unavailable", f"No Tailscale peer named {machine}")

    async def forward(self, envelope):
        kind, machine, target = address(envelope["to"])
        base = await self.resolve(machine)
        params = {k: envelope[k] for k in ("id", "from", "type", "corr", "payload")}
        params["to"] = f"{kind}:{target}"
        async with self.session.post(base + "/v1/send", json=params, allow_redirects=False) as r:
            data = await r.json()
            if r.status != 200 or "error" in data:
                error = data.get("error", {})
                raise BusError(error.get("code", "remote_error"), error.get("message", f"HTTP {r.status}"))
        return data

    async def expose(self, enabled):
        await self.refresh()
        if enabled and self.error:
            raise BusError("unavailable", self.error)
        # Never replace another application's Serve handler, even on our default port.
        configured = json.loads(await command("tailscale", "serve", "status", "--json", timeout=3))
        port = f":{self.config.network_port}"
        expected = f"http://127.0.0.1:{self.config.gateway_port}"
        for host, definition in configured.get("Web", {}).items():
            if host.endswith(port) and expected not in json.dumps(definition):
                raise BusError(
                    "port_in_use", f"Tailscale port {self.config.network_port} belongs to another service"
                )
        argv = ["tailscale", "serve", "--bg", f"--https={self.config.network_port}"]
        if enabled and self.config.gateway_port is None:
            raise BusError("unavailable", "HTTP gateway is not configured")
        argv += [expected] if enabled else ["off"]
        try:
            await command(*argv, timeout=20)
        except TimeoutError as e:
            raise BusError("timeout", "Tailscale Serve timed out; check `tailscale serve status`") from e
        self.store.set_meta("exposed", "true" if enabled else "false")
        return await self.exposure()

    async def exposure(self):
        try:
            data = json.loads(await command("tailscale", "serve", "status", "--json", timeout=3))
            expected = f"http://127.0.0.1:{self.config.gateway_port}"
            active = any(
                expected in json.dumps(v)
                for k, v in data.get("Web", {}).items()
                if k.endswith(f":{self.config.network_port}")
            )
        except (BusError, ValueError, TimeoutError):
            active = False
        return {
            "enabled": active,
            "url": f"https://{self.machine}:{self.config.network_port}" if active else None,
            "port": self.config.network_port,
        }

    async def principal(self, headers):
        # Unix/loopback backends are private to this machine. Like Tailscale Serve,
        # ABUS trusts local processes when accepting forwarded identity headers.
        ip = headers.get("X-Forwarded-For", "").split(",")[0].strip()
        user = headers.get("Tailscale-User-Login")
        if not ip:
            raise BusError("forbidden", "Tailscale Serve source identity is required")
        connector = aiohttp.UnixConnector(path="/var/run/tailscale/tailscaled.sock")
        try:
            async with aiohttp.ClientSession(
                connector=connector, timeout=aiohttp.ClientTimeout(total=3)
            ) as client:
                async with client.get(
                    "http://local-tailscaled.sock/localapi/v0/whois", params={"addr": ip}
                ) as r:
                    if r.status != 200:
                        raise BusError("forbidden", "Cannot establish tailnet node identity")
                    who = await r.json()
        except (aiohttp.ClientError, ValueError, TimeoutError) as e:
            raise BusError("forbidden", "Cannot establish tailnet node identity") from e
        node = who.get("Node", {})
        machine = node.get("Name", "").rstrip(".")
        if not machine:
            raise BusError("forbidden", "Unknown tailnet source")
        return {
            "machine": machine,
            "user": user or who.get("UserProfile", {}).get("LoginName") or "tagged-node",
        }

    @staticmethod
    def remote_sender(sender, principal):
        machine = principal["machine"]
        if sender.startswith("agent:"):
            _, _, name = address(sender)
            return f"agent:{machine}/{name}"
        return f"remote:{machine}/{sender}"[:256]
