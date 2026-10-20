import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.client.stdio import stdio_client

from abus.client import call
from mcp import ClientSession, StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]


def environment(bus):
    return {
        **os.environ,
        "XDG_RUNTIME_DIR": str(bus.config.runtime),
        "XDG_STATE_HOME": str(bus.config.state.parents[1]),
    }


async def cli(bus, *args, input=None):
    p = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "bin/omarchy-abus"),
        *args,
        env=environment(bus),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(p.communicate(input), 10)
    return p.returncode, stdout, stderr


async def test_cli_stdin_capabilities_error_exit_codes(bus):
    rc, out, err = await cli(bus, "register", "reviewer", "--capabilities", "python", "review")
    assert rc == 0
    assert json.loads(out)["address"] == "agent:reviewer"
    assert bus.store.agents()[0]["capabilities"] == ["python", "review"]
    rc, out, err = await cli(
        bus, "send", "agent:reviewer", "prompt", "-", "--corr", "c1", input=b'{"text":"hello"}'
    )
    assert rc == 0
    assert bus.store.inbox({"name": "reviewer"})["messages"][0]["corr"] == "c1"
    rc, _, err = await cli(bus, "send", "agent:missing", "prompt")
    assert rc == 1
    assert json.loads(err)["error"]["code"] == "unregistered"


async def test_cli_receiver_process_and_mcp_exchange_a_correlated_request_reply(bus, tmp_path):
    # MCP harness sends; an independently running CLI harness receives and replies.
    server = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / "mcp/abus_mcp.py")], env=environment(bus)
    )
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        required = {"abus_register", "abus_send", "abus_inbox", "abus_list", "abus_ack", "abus_machines"}
        assert required <= {t.name for t in tools.tools}
        for name in ("mcp-sender", "cli-receiver"):
            assert not (await session.call_tool("abus_register", {"name": name})).isError
        handler = tmp_path / "receiver.py"
        handler.write_text(
            """import json,sys\nfrom abus.client import call\nm=json.load(sys.stdin)\ncall("send", {"to":m["from"],"from":"agent:cli-receiver","type":"reply","corr":m["corr"] or m["id"],"payload":{"text":"review complete"}})\n"""
        )
        receiver = asyncio.create_task(
            cli(bus, "listen", "cli-receiver", "--once", "--exec", sys.executable, str(handler))
        )
        response = await session.call_tool(
            "abus_send",
            {
                "to": "agent:cli-receiver",
                "type": "request",
                "sender": "agent:mcp-sender",
                "corr": "review-42",
                "payload": {"text": "review this"},
            },
        )
        assert not response.isError
        rc, _, err = await receiver
        assert rc == 0, err
        inbox = await session.call_tool("abus_inbox", {"name": "mcp-sender"})
        data = json.loads(inbox.content[0].text)
        reply = data["messages"][0]
        assert reply["corr"] == "review-42"
        assert reply["payload"]["text"] == "review complete"
        assert not (await session.call_tool("abus_ack", {"id": reply["id"], "name": "mcp-sender"})).isError
        assert not bus.store.inbox({"name": "cli-receiver"})["messages"]
        assert not bus.store.inbox({"name": "mcp-sender"})["messages"]


async def test_failed_receiver_keeps_message_for_retry(bus):
    await asyncio.to_thread(call, "register", {"name": "receiver"}, config=bus.config)
    await asyncio.to_thread(call, "send", {"to": "agent:receiver", "type": "prompt"}, config=bus.config)
    rc, _, _ = await cli(
        bus, "listen", "receiver", "--once", "--exec", sys.executable, "-c", "raise SystemExit(3)"
    )
    assert rc == 3
    assert bus.store.inbox({"name": "receiver"})["messages"]
