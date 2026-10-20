#!/usr/bin/env python3
"""Remove ABUS's user installation and retain message history."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

MARKER = "# ABUS managed Omarchy dispatcher"


def uninstall(files_only=False):
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    plugin = config / "omarchy/plugins/syndicalt.abus"
    manifest = plugin / "manifest.json"
    if not manifest.exists() or json.loads(manifest.read_text()).get("id") != "syndicalt.abus":
        raise RuntimeError("No managed ABUS plugin was found")
    if not files_only:
        cli = home / ".local/bin/omarchy-abus"
        if cli.exists():
            result = subprocess.run([str(cli), "expose", "off"], capture_output=True, text=True)
            if (
                result.returncode
                and '"enabled": true'
                in subprocess.run([str(cli), "expose", "status"], capture_output=True, text=True).stdout
            ):
                raise RuntimeError("Disable ABUS tailnet exposure before uninstalling")
        subprocess.run(["omarchy-plugin-disable", "syndicalt.abus"], check=True)
        subprocess.run(["systemctl", "--user", "disable", "--now", "abus.service"], check=True)
    for directory in (home / ".codex/skills", home / ".claude/skills", home / ".agents/skills"):
        link = directory / "abus"
        if link.is_symlink() and link.resolve() == plugin / "skill":
            link.unlink()
    shim = home / ".local/bin/omarchy"
    if shim.exists() and MARKER in shim.read_text():
        shim.unlink()
    cli = home / ".local/bin/omarchy-abus"
    if cli.exists() and str(plugin / "bin/omarchy-abus") in cli.read_text():
        cli.unlink()
    for path in (config / "systemd/user/abus.service", data / "applications/syndicalt.abus.desktop"):
        if path.exists() and str(plugin) in path.read_text():
            path.unlink()
    if (data / "abus/venv").exists():
        shutil.rmtree(data / "abus/venv")
    (data / "abus/mcp.json").unlink(missing_ok=True)
    shutil.rmtree(plugin)
    if not files_only:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["omarchy-shell", "shell", "rescanPlugins"], check=True)
    print("ABUS removed. Message history is retained under the XDG state directory.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files-only", action="store_true", help="Remove files without contacting systemd or Omarchy"
    )
    args = parser.parse_args()
    try:
        uninstall(args.files_only)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"ABUS uninstall failed: {error}\n")
