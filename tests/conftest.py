from unittest.mock import AsyncMock

import pytest

from abus.config import Config
from abus.daemon import Bus


@pytest.fixture
async def bus(tmp_path, monkeypatch):
    config = Config(tmp_path / "run", tmp_path / "state", poll_interval=0.1)
    daemon = Bus(config)
    monkeypatch.setattr(daemon.herdr, "refresh", AsyncMock(return_value=[]))
    monkeypatch.setattr(daemon.network, "refresh", AsyncMock())
    monkeypatch.setattr(daemon.network, "exposure", AsyncMock(return_value={"enabled": False}))
    await daemon.start()
    try:
        yield daemon
    finally:
        await daemon.close()
