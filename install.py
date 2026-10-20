#!/usr/bin/env python3
"""Stage and validate a user-owned installation before publishing its files."""

import argparse
import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from abus.installation import MARKER, digest, managed, publish, receipt

ROOT = Path(__file__).resolve().parent
OMARCHY = Path("/usr/share/omarchy/bin/omarchy")
IGNORE = shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist")


def run(*args):
    subprocess.run(args, check=True)


def systemd_argument(path):
    return (
        '"' + str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'
    )


def installation_files(target, python, config, data, bins):
    files = {}

    def write(path, content, executable=False):
        files[path] = (content.encode(), 0o755 if executable else 0o600)

    write(
        bins / "omarchy-abus",
        "#!/bin/sh\nexec "
        + shlex.quote(str(python))
        + " "
        + shlex.quote(str(target / "bin/omarchy-abus"))
        + ' "$@"\n',
        True,
    )
    write(
        bins / "omarchy",
        "#!/bin/sh\n"
        + MARKER
        + '\nif [ "${1-}" = abus ]; then\n  shift\n  exec '
        + shlex.quote(str(bins / "omarchy-abus"))
        + ' "$@"\nfi\nexec '
        + shlex.quote(str(OMARCHY))
        + ' "$@"\n',
        True,
    )
    write(
        config / "systemd/user/abus.service",
        f"""[Unit]
Description=ABUS durable agent messaging
After=network.target

[Service]
Type=simple
ExecStart={systemd_argument(python)} {systemd_argument(target / "plugin/bin/abusd.py")}
Restart=on-failure
RestartSec=2
TimeoutStopSec=10
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
""",
    )
    desktop_cli = str(bins / "omarchy-abus").replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        desktop_cli = desktop_cli.replace(char, "\\" + char)
    desktop_cli = desktop_cli.replace("\\", "\\\\")
    write(
        data / "applications/syndicalt.abus.desktop",
        f'''[Desktop Entry]
Type=Application
Name=ABUS
Comment=Agent mailboxes and delivery across your Omarchy machines
Exec="{desktop_cli}" ui
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
    return files


def prepare_environment(environment, lock):
    fingerprint = digest(lock.read_bytes())
    marker = environment / ".abus-environment"
    if environment.is_symlink() or (environment.exists() and not marker.is_file()):
        raise RuntimeError(f"Refusing to reuse an unrelated environment: {environment}")
    if environment.exists():
        if marker.read_text() != fingerprint:
            raise RuntimeError(f"Environment fingerprint does not match: {environment}")
        return
    environment.parent.mkdir(parents=True, exist_ok=True)
    try:
        run("uv", "venv", "--python", "3.12", str(environment))
        run("uv", "pip", "sync", "--python", str(environment / "bin/python"), str(lock))
        environment.mkdir(exist_ok=True)
        marker.write_text(fingerprint)
    except BaseException:
        if environment.exists():
            shutil.rmtree(environment)
        raise


def install(args):
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    target = config / "omarchy/plugins/syndicalt.abus"
    bins, namespace = home / ".local/bin", data / "abus"
    record_path = namespace / "installation.json"
    record = receipt(record_path)
    lock = ROOT / "requirements.lock"
    environment = namespace / "environments" / (digest(lock.read_bytes())[:16] + "-py312")
    files = installation_files(target, environment / "bin/python", config, data, bins)
    for path in files:
        if not managed(path, record, target, data, bins):
            raise RuntimeError(f"{path} already exists and is not managed by ABUS or was modified")
    if target.is_symlink() or (
        target.exists()
        and (
            not (target / "manifest.json").is_file()
            or json.loads((target / "manifest.json").read_text()).get("id") != "syndicalt.abus"
        )
    ):
        raise RuntimeError(f"Refusing to overwrite unrelated directory {target}")
    if not OMARCHY.is_file() or not shutil.which("uv") or not shutil.which("qs"):
        raise RuntimeError("Installation requires Omarchy, uv and Quickshell")
    namespace.mkdir(mode=0o700, parents=True, exist_ok=True)
    if namespace.is_symlink() or namespace.stat().st_uid != os.getuid():
        raise RuntimeError(f"Invalid ABUS data directory: {namespace}")
    namespace.chmod(0o700)
    descriptor = os.open(namespace / "install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as installation_lock:
        try:
            fcntl.flock(installation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another ABUS installation is in progress") from error
        if receipt(record_path) != record:
            raise RuntimeError("The installation changed; retry from its current state")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".abus-stage-", dir=target.parent) as directory:
            staged = Path(directory) / "plugin"
            if target.exists() and target.resolve() != ROOT:
                shutil.copytree(target, staged, symlinks=True, ignore=IGNORE)
            shutil.copytree(ROOT, staged, dirs_exist_ok=True, symlinks=True, ignore=IGNORE)
            run("omarchy-plugin-validate", str(staged))
            prepare_environment(environment, staged / "requirements.lock")
            run(str(environment / "bin/python"), str(staged / "bin/omarchy-abus"), "--help")
            run(
                str(environment / "bin/python"),
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); import abus.daemon, abus.mcp",
                str(staged),
            )
            for path in files:
                if not managed(path, record, target, data, bins):
                    raise RuntimeError(f"Installation target changed while staging: {path}")
            environments = list(dict.fromkeys([str(environment), *record.get("environments", [])]))
            new_record = {
                "version": 1,
                "files": {str(path): digest(content) for path, (content, _) in files.items()},
                "environments": environments,
            }
            files[record_path] = ((json.dumps(new_record, indent=2) + "\n").encode(), 0o600)
            if target.exists() and (target / ".git").exists() and target.resolve() != ROOT:
                git = target / ".git"
                if git.is_dir():
                    shutil.copytree(git, staged / ".git", symlinks=True)
                else:
                    shutil.copy2(git, staged / ".git")
            publish(files, target, staged if target.resolve() != ROOT else None)
        for directory in (home / ".codex/skills", home / ".claude/skills", home / ".agents/skills"):
            link = directory / "abus"
            directory.mkdir(parents=True, exist_ok=True)
            if not link.exists() and not link.is_symlink():
                link.symlink_to(target / "skill", target_is_directory=True)
        if not args.no_start:
            run("systemctl", "--user", "daemon-reload")
            run("systemctl", "--user", "enable", "--now", "abus.service")
            run("systemctl", "--user", "restart", "abus.service")
            for _ in range(50):
                probe = subprocess.run(
                    [str(environment / "bin/python"), str(target / "bin/omarchy-abus"), "list"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if probe.returncode == 0:
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Files installed, but the ABUS service did not become ready")
            run("omarchy-shell", "shell", "rescanPlugins")
            run("omarchy-plugin-enable", "syndicalt.abus")
    print(f"Installed ABUS in {target}\nOpen ABUS from your launcher, or run: omarchy abus ui")
    print(f"MCP configuration: {namespace / 'mcp.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Install files without starting services or enabling the widget",
    )
    try:
        install(parser.parse_args())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"ABUS installation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
