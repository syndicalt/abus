#!/usr/bin/env python3
"""Install only user-owned files; never modify the Omarchy package."""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MARKER = "# ABUS managed Omarchy dispatcher"


def run(*args):
    subprocess.run(args, check=True)


def write(path, content, executable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if executable:
        path.chmod(0o755)


def install(args):
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    target = config / "omarchy/plugins/syndicalt.abus"
    bins = home / ".local/bin"
    shim = bins / "omarchy"
    # Validate collisions before modifying installation state.
    if shim.exists() and MARKER not in shim.read_text():
        raise RuntimeError(f"{shim} already exists and is not managed by ABUS")
    original = "/usr/share/omarchy/bin/omarchy"
    if not Path(original).is_file():
        raise RuntimeError("This installer requires Omarchy")
    if not shutil.which("uv"):
        raise RuntimeError("Install uv first: omarchy pkg add uv")
    if not shutil.which("qs"):
        raise RuntimeError("Quickshell is required")
    if target.resolve() != ROOT:
        if target.exists() and not (target / "abus/__init__.py").exists():
            raise RuntimeError(f"Refusing to overwrite unrelated directory {target}")
        shutil.copytree(
            ROOT,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"
            ),
        )
    run("omarchy-plugin-validate", str(target))
    venv = data / "abus/venv"
    if not (venv / "bin/python").exists():
        run("uv", "venv", "--python", "3.12", str(venv))
    run("uv", "pip", "sync", "--python", str(venv / "bin/python"), str(target / "requirements.lock"))
    cli = target / "bin/omarchy-abus"
    python = venv / "bin/python"
    write(
        bins / "omarchy-abus",
        "#!/bin/sh\nexec " + shlex.quote(str(python)) + " " + shlex.quote(str(cli)) + ' "$@"\n',
        True,
    )
    write(
        shim,
        "#!/bin/sh\n"
        + MARKER
        + '\nif [ "${1-}" = abus ]; then\n  shift\n  exec '
        + shlex.quote(str(bins / "omarchy-abus"))
        + ' "$@"\nfi\nexec '
        + shlex.quote(original)
        + ' "$@"\n',
        True,
    )
    write(
        config / "systemd/user/abus.service",
        f'''[Unit]
Description=ABUS durable agent messaging
After=network.target

[Service]
Type=simple
ExecStart="{python}" "{target / "plugin/bin/abusd.py"}"
WorkingDirectory={target}
Restart=on-failure
RestartSec=2
TimeoutStopSec=10
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
''',
    )
    write(
        data / "applications/syndicalt.abus.desktop",
        f'''[Desktop Entry]
Type=Application
Name=ABUS
Comment=Agent mailboxes and delivery across your Omarchy machines
Exec="{bins / "omarchy-abus"}" ui
Icon={target / "assets/abus.svg"}
Terminal=false
Categories=Development;
StartupWMClass=syndicalt.abus
''',
    )
    write(
        data / "abus/mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "abus": {
                        "command": str(python),
                        "args": [str(target / "mcp/abus_mcp.py")],
                        "env": {
                            "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
                        },
                    }
                }
            },
            indent=2,
        )
        + "\n",
    )
    # Harnesses load this portable skill from their normal discovery directories.
    for directory in (home / ".codex/skills", home / ".claude/skills", home / ".agents/skills"):
        link = directory / "abus"
        directory.mkdir(parents=True, exist_ok=True)
        if not link.exists() and not link.is_symlink():
            link.symlink_to(target / "skill", target_is_directory=True)
    if not args.no_start:
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "enable", "--now", "abus.service")
        run("systemctl", "--user", "restart", "abus.service")
        # Wait for the socket before loading the read-only monitor widget.
        import time

        for _ in range(50):
            probe = subprocess.run([str(python), str(cli), "list"], capture_output=True, text=True)
            if probe.returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("The installed ABUS service did not become ready")
        run("omarchy-shell", "shell", "rescanPlugins")
        run("omarchy-plugin-enable", "syndicalt.abus")
    print(f"Installed ABUS in {target}\nOpen ABUS from your launcher, or run: omarchy abus ui")
    print(f"MCP configuration: {data / 'abus/mcp.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Install files without starting services or enabling the widget",
    )
    try:
        install(parser.parse_args())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ABUS installation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
