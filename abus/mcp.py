"""One stdio adapter per harness; all adapters use the same durable daemon."""

import asyncio

from mcp.server.fastmcp import FastMCP

from .client import call

server = FastMCP(
    "ABUS",
    instructions="Register a stable agent name, send typed messages, and acknowledge your inbox after handling each message. Use corr to connect requests and replies.",
)


async def rpc(method, **params):
    return await asyncio.to_thread(call, method, {k: v for k, v in params.items() if v is not None})


@server.tool()
async def abus_register(
    name: str,
    capabilities: list[str] | None = None,
    project: str | None = None,
    session: str | None = None,
    endpoint: str | None = None,
) -> dict:
    """Register a durable mailbox; optional herdr:<pane> endpoint enables prompt notifications."""
    return await rpc(
        "register",
        name=name,
        capabilities=capabilities or [],
        project=project,
        session=session,
        endpoint=endpoint,
    )


@server.tool()
async def abus_unregister(name: str) -> dict:
    """Remove registration and subscriptions; preserve retained inbox messages."""
    return await rpc("unregister", name=name)


@server.tool()
async def abus_send(
    to: str, type: str, payload: dict, sender: str = "mcp", corr: str | None = None, id: str | None = None
) -> dict:
    """Send prompt/reply/status/event/request/ack. Remote: agent:<machine>/<name>.

    Reuse id for retries; use corr (or the request id) in replies. Accepted means stored, not task complete.
    """
    return await rpc(
        "send", **{"to": to, "type": type, "payload": payload, "from": sender, "corr": corr, "id": id}
    )


@server.tool()
async def abus_inbox(name: str, limit: int = 100, after: int = 0, wait: float = 0) -> dict:
    """Read unacknowledged messages. Follow next while more=true. wait is 0..25 seconds."""
    return await rpc("inbox", name=name, limit=limit, after=after, wait=wait)


@server.tool()
async def abus_claim(name: str, owner: str, limit: int = 1, lease: int = 60, wait: float = 0) -> dict:
    """Claim messages for an exclusive receiver. Supply owner again on ack or renewal."""
    return await rpc("claim", name=name, owner=owner, limit=limit, lease=lease, wait=wait)


@server.tool()
async def abus_ack(id: str, name: str | None = None, owner: str | None = None) -> dict:
    """Acknowledge a handled message; name is required for broadcasts, owner for active claims."""
    return await rpc("ack", id=id, name=name, owner=owner)


@server.tool()
async def abus_renew(id: str, name: str, owner: str) -> dict:
    """Extend a receiver claim by 60 seconds while doing work."""
    return await rpc("renew", id=id, name=name, owner=owner)


@server.tool()
async def abus_release(id: str, name: str, owner: str) -> dict:
    """Release a claim for another delivery attempt."""
    return await rpc("release", id=id, name=name, owner=owner)


@server.tool()
async def abus_list() -> dict:
    """Discover registered agents, capabilities, unread counts, and live Herdr panes."""
    return await rpc("list")


@server.tool()
async def abus_machines() -> dict:
    """Discover reachable Tailscale peers running ABUS."""
    return await rpc("machines")


@server.tool()
async def abus_status() -> dict:
    """Read daemon health, delivery counts, and actual Tailscale exposure state."""
    return await rpc("status")


@server.tool()
async def abus_subscribe(name: str, topic: str) -> dict:
    """Subscribe to broadcast:status or broadcast:events. Acknowledgments are per agent."""
    return await rpc("subscribe", name=name, topic=topic)


@server.tool()
async def abus_unsubscribe(name: str, topic: str) -> dict:
    """Stop receiving new messages on a broadcast topic."""
    return await rpc("unsubscribe", name=name, topic=topic)


@server.tool()
async def abus_heartbeat(name: str) -> dict:
    """Refresh presence without changing durable registration."""
    return await rpc("heartbeat", name=name)


@server.tool()
async def abus_history(to: str | None = None, limit: int = 100, before: int = 0) -> dict:
    """Inspect retained envelopes and delivery errors. Follow next while more=true to read older messages."""
    return await rpc("history", to=to, limit=limit, before=before)


@server.tool()
async def abus_retry(id: str) -> dict:
    """Retry a queued or failed delivery after addressing its error."""
    return await rpc("retry", id=id)


@server.tool()
async def abus_expose(enabled: bool) -> dict:
    """Enable or disable private Tailscale transport when authorized by the user."""
    return await rpc("expose", enabled=enabled)


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
