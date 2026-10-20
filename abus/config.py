import os
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    runtime: Path
    state: Path
    network_port: int = 8743
    poll_interval: float = 5.0
    gateway_port: int | None = None
    delivery_concurrency: int = 8

    def __post_init__(self):
        for name in ("network_port", "gateway_port"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not 1 <= value <= 65535):
                raise ValueError(f"{name} must be a port in 1..65535")
        if not 0.05 <= self.poll_interval <= 300:
            raise ValueError("poll_interval must be 0.05..300 seconds")
        if type(self.delivery_concurrency) is not int or not 1 <= self.delivery_concurrency <= 32:
            raise ValueError("delivery_concurrency must be 1..32")

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
        info = self.runtime.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError(
                f"Runtime directory must be owned by this user and private (0700): {self.runtime}"
            )
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError(f"State directory must be owned by this user and not a symlink: {self.state}")
        self.state.chmod(0o700)


class BusError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code

    def as_dict(self):
        return {"code": self.code, "message": str(self)}
