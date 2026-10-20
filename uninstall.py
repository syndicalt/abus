#!/usr/bin/env python3
"""Remove owned application files only after tailnet exposure is verified disabled."""

import argparse
import fcntl
import json
import os
import shutil
import subprocess
from pathlib import Path

from abus.installation import managed, receipt


def uninstall(files_only=False):
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    plugin = config / "omarchy/plugins/syndicalt.abus"
    namespace, bins = data / "abus", home / ".local/bin"
    manifest = plugin / "manifest.json"
    if (
        plugin.is_symlink()
        or not manifest.is_file()
        or json.loads(manifest.read_text()).get("id") != "syndicalt.abus"
    ):
        raise RuntimeError("No managed ABUS plugin was found")
    record_path = namespace / "installation.json"
    record = receipt(record_path)
    files = [
        bins / "omarchy",
        bins / "omarchy-abus",
        config / "systemd/user/abus.service",
        data / "applications/syndicalt.abus.desktop",
        namespace / "mcp.json",
    ]
    for path in files:
        if not managed(path, record, plugin, data, bins):
            raise RuntimeError(f"Refusing to remove an unrelated or modified file: {path}")
    environments = [Path(path) for path in record.get("environments", [])]
    for environment in environments:
        if (
            environment.parent != namespace / "environments"
            or environment.is_symlink()
            or (environment.exists() and not (environment / ".abus-environment").is_file())
        ):
            raise RuntimeError(f"Refusing to remove an unrelated environment: {environment}")
    legacy = namespace / "venv"
    cli = bins / "omarchy-abus"
    if (
        not record
        and cli.exists()
        and str(legacy / "bin/python") in cli.read_text()
        and not legacy.is_symlink()
    ):
        environments.append(legacy)
    namespace.mkdir(parents=True, exist_ok=True)
    if namespace.is_symlink() or namespace.stat().st_uid != os.getuid():
        raise RuntimeError(f"Invalid ABUS data directory: {namespace}")
    descriptor = os.open(namespace / "install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as installation_lock:
        try:
            fcntl.flock(installation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another ABUS installation is in progress") from error
        if receipt(record_path) != record:
            raise RuntimeError("The installation changed; retry from its current state")
        if not files_only:
            if not cli.is_file():
                raise RuntimeError(
                    "Cannot verify exposure without the installed CLI; preserve the installation"
                )
            result = subprocess.run([str(cli), "expose", "off"], capture_output=True, text=True, timeout=30)
            try:
                disabled = json.loads(result.stdout).get("enabled") is False
            except (ValueError, AttributeError):
                disabled = False
            if result.returncode or not disabled:
                raise RuntimeError("Cannot verify disabled ABUS exposure; installation retained")
            subprocess.run(["omarchy-plugin-disable", "syndicalt.abus"], check=True, timeout=15)
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", "abus.service"], check=True, timeout=15
            )
        for directory in (home / ".codex/skills", home / ".claude/skills", home / ".agents/skills"):
            link = directory / "abus"
            if link.is_symlink() and link.resolve() == (plugin / "skill").resolve():
                link.unlink()
        for path in files:
            path.unlink(missing_ok=True)
        for environment in environments:
            if environment.exists():
                shutil.rmtree(environment)
        record_path.unlink(missing_ok=True)
        shutil.rmtree(plugin)
        if not files_only:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=15)
            subprocess.run(["omarchy-shell", "shell", "rescanPlugins"], check=True, timeout=15)
    print("ABUS removed. Message history is retained under the XDG state directory.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files-only",
        action="store_true",
        help="Remove files without contacting services or changing exposure",
    )
    args = parser.parse_args()
    try:
        uninstall(args.files_only)
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        parser.exit(1, f"ABUS uninstall failed: {error}\n")
