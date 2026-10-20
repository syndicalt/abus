"""Provision an enabled plugin in a supervised, independent systemd setup job."""

import os
import subprocess
import sys
from pathlib import Path

from .installation import setup_status

UNIT = "abus-setup.service"


def active(unit):
    state = subprocess.run(
        ["systemctl", "--user", "show", unit, "--property=ActiveState", "--value"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return state.returncode == 0 and state.stdout.strip() in {"active", "activating", "reloading"}


def launch(source, *, unit=UNIT):
    if active(unit):
        print("ABUS setup is already running")
        return 0
    keys = (
        "HOME",
        "PATH",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "UV_LINK_MODE",
        "ABUS_HTTP_PORT",
        "ABUS_NETWORK_PORT",
        "ABUS_HERDR_BIN",
        "ABUS_HERDR_SESSION",
        "ABUS_HERDR_ARTIFACT",
        "ABUS_TAILSCALE_SOCKET",
    )
    environment = [f"--setenv={key}={os.environ[key]}" for key in keys if key in os.environ]
    result = subprocess.run(
        [
            "systemd-run",
            "--user",
            "--quiet",
            "--collect",
            "--wait",
            f"--unit={unit}",
            "--property=Type=oneshot",
            "--property=TimeoutStartSec=10min",
            "--property=UMask=0077",
            "--property=NoNewPrivileges=true",
            *environment,
            sys.executable,
            str(source / "install.py"),
            "--ensure",
        ],
        timeout=620,
    )
    # Another shell instance may have won the transient-unit creation race.
    return 0 if result.returncode and active(unit) else result.returncode


def main():
    source = Path(__file__).resolve().parent.parent
    try:
        result = launch(source)
        if result:
            setup_status("failed", "Setup job failed; inspect journalctl --user -u abus-setup.service")
        raise SystemExit(result)
    except (OSError, subprocess.TimeoutExpired) as error:
        setup_status("failed", str(error)[:1000])
        print(f"ABUS setup could not start: {error}", file=sys.stderr)
        raise SystemExit(1) from error
