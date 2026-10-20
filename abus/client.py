"""Small dependency-free local client, shared by CLI and MCP."""

import json
import socket
import uuid

from .config import BusError, Config


def call(method, params=None, *, config=None, timeout=30):
    config = config or Config.environment()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(config.socket))
            request = {"id": str(uuid.uuid4()), "method": method, "params": params or {}}
            connection.sendall((json.dumps(request, allow_nan=False) + "\n").encode())
            with connection.makefile("rb") as stream:
                line = stream.readline(16 * 1024 * 1024)
            if not line.endswith(b"\n"):
                raise BusError("protocol_error", "Incomplete or oversized daemon response")
            response = json.loads(line)
    except (OSError, ValueError) as e:
        raise BusError("unavailable", f"Cannot reach ABUS: {e}. Run `systemctl --user start abus`.") from e
    if "error" in response:
        error = response["error"]
        raise BusError(error.get("code", "error"), error.get("message", str(error)))
    return response["result"]
