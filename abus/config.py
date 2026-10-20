import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    runtime: Path
    state: Path
    network_port: int = 8743
    poll_interval: float = 5.0
    gateway_port: int | None = None

    @classmethod
    def environment(cls):
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/abus-{os.getuid()}"))
        state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy/abus"
        return cls(
            runtime,
            state,
            int(os.environ.get("ABUS_NETWORK_PORT", "8743")),
            gateway_port=int(os.environ.get("ABUS_HTTP_PORT", "8744")),
        )

    @property
    def socket(self):
        return self.runtime / "abus.sock"

    @property
    def http_socket(self):
        return self.runtime / "abus-http.sock"

    def prepare(self):
        self.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state.chmod(0o700)


class BusError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def as_dict(self):
        return {"code": self.code, "message": str(self)}
