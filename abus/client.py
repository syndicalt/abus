"""Small dependency-free local client, shared by CLI and MCP."""

import json
import socket
import uuid

from .config import BusError, Config
from .protocol import loads


def call(method, params=None, *, config=None, timeout=30):
    config = config or Config.environment()
    request = {"id": str(uuid.uuid4()), "method": method, "params": params or {}}
    try:
        wire = (json.dumps(request, allow_nan=False) + "\n").encode()
    except (ValueError, TypeError, RecursionError) as error:
        raise BusError("invalid_params", "Request must contain finite JSON values") from error
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(str(config.socket))
            connection.sendall(wire)
            with connection.makefile("rb") as stream:
                line = stream.readline(16 * 1024 * 1024)
            if not line.endswith(b"\n"):
                raise BusError("protocol_error", "Incomplete or oversized daemon response")
            response = loads(line)
    except OSError as e:
        raise BusError("unavailable", f"Cannot reach ABUS: {e}. Run `systemctl --user start abus`.") from e
    except (ValueError, RecursionError) as e:
        raise BusError("protocol_error", "Daemon returned invalid JSON") from e
    if (
        not isinstance(response, dict)
        or response.get("id") != request["id"]
        or ("result" in response) == ("error" in response)
    ):
        raise BusError("protocol_error", "Daemon response does not match the request")
    if "error" in response:
        error = response["error"]
        if (
            not isinstance(error, dict)
            or not isinstance(error.get("code"), str)
            or not isinstance(error.get("message"), str)
        ):
            raise BusError("protocol_error", "Invalid daemon error response")
        raise BusError(error.get("code", "error"), error.get("message", str(error)))
    return response["result"]
