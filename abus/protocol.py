"""Strict JSON framing and bounded responses shared by transport adapters."""

import json

from .config import BusError


def reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def loads(value):
    return json.loads(value, parse_constant=reject_constant)


async def read_json(response, limit, *, nullable=False):
    body = bytearray()
    async for chunk in response.content.iter_chunked(min(limit + 1, 16384)):
        body.extend(chunk)
        if len(body) > limit:
            raise BusError("protocol_error", f"Response exceeds {limit} bytes")
    try:
        value = loads(body)
    except (ValueError, RecursionError) as error:
        raise BusError("protocol_error", "Response is not valid finite JSON") from error
    if value is None and nullable:
        return {}
    if not isinstance(value, dict):
        raise BusError("protocol_error", "Expected a JSON response object")
    return value
