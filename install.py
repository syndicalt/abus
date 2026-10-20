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

from abus.companion import prepare as prepare_companion
from abus.installation import (
    MARKER,
    atomic_write,
    digest,
    managed,
    publish,
    receipt,
    setup_status,
    source_digest,
)

ROOT = Path(__file__).resolve().parent
OMARCHY = Path("/usr/share/omarchy/bin/omarchy")
IGNORE = shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "dist")


def run(*args):
    subprocess.run(args, check=True)


def activate(python, target, *, changed, ensure):
    if changed:
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "enable", "abus.service")
        run("systemctl", "--user", "enable", "--now", "abus-cleanup.path")
    run("systemctl", "--user", "restart" if changed else "start", "abus.service")
    for _ in range(50):
        probe = subprocess.run(
            [str(python), str(target / "bin/omarchy-abus"), "list"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if probe.returncode == 0:
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Files installed, but the ABUS service did not become ready")
    if not ensure:
        run("omarchy-shell", "shell", "rescanPlugins")
        run("omarchy-plugin-enable", "syndicalt.abus")


def systemd_argument(path):
    return (
        '"' + str(path).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'
    )


def service_environment():
    keys = (
        "HOME",
        "PATH",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "ABUS_HTTP_PORT",
        "ABUS_NETWORK_PORT",
        "ABUS_HERDR_BIN",
        "ABUS_HERDR_SESSION",
        "ABUS_TAILSCALE_SOCKET",
    )
    lines = []
    for key in keys:
        if key in os.environ:
            value = os.environ[key].replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
            value = value.replace("\n", "\\n").replace("\r", "\\r")
            lines.append(f'Environment="{key}={value}"')
    return "\n".join(lines)


def installation_files(target, python, config, data, bins, runtime_root=None, *, managed_herdr=True):
    runtime_root = runtime_root or target
    files = {}

    def write(path, content, executable=False):
        files[path] = (content.encode(), 0o755 if executable else 0o600)

    write(
        bins / "omarchy-abus",
        "#!/bin/sh\nexec "
        + shlex.quote(str(python))
        + " "
        + shlex.quote(str(runtime_root / "bin/omarchy-abus"))
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
{service_environment()}
Environment="ABUS_HERDR_MANAGED={int(managed_herdr)}"
ExecStart={systemd_argument(python)} {systemd_argument(runtime_root / "plugin/bin/abusd.py")}
Restart=on-failure
RestartSec=2
TimeoutStopSec=10
UMask=0077
NoNewPrivileges=true

[Install]
WantedBy=default.target
""",
    )
    write(
        config / "systemd/user/abus-cleanup.service",
        f"""[Unit]
Description=Remove ABUS services after its plugin is removed

[Service]
Type=oneshot
{service_environment()}
ExecStart={systemd_argument(python)} {systemd_argument(runtime_root / "uninstall.py")} --if-removed
Restart=on-failure
RestartSec=30
UMask=0077
NoNewPrivileges=true
""",
    )
    parent = str(target.parent).replace("%", "%%")
    if any(char in parent for char in "\r\n\0"):
        raise RuntimeError("Plugin path must not contain control characters")
    write(
        config / "systemd/user/abus-cleanup.path",
        f"""[Unit]
Description=Watch for removal of the ABUS plugin

[Path]
PathChanged={parent}
Unit=abus-cleanup.service

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
Icon={runtime_root / "assets/abus.svg"}
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
                        "args": [str(runtime_root / "mcp/abus_mcp.py")],
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


def prepare_runtime(staged, runtime_root, fingerprint):
    if runtime_root.is_symlink():
        raise RuntimeError(f"Refusing a symlink runtime: {runtime_root}")
    if runtime_root.exists():
        marker = runtime_root / ".abus-runtime"
        if not marker.is_file() or marker.read_text() != fingerprint:
            raise RuntimeError(f"Refusing an unrelated runtime: {runtime_root}")
        return
    runtime_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stage-", dir=runtime_root.parent) as directory:
        prepared = Path(directory) / "runtime"
        shutil.copytree(staged, prepared, symlinks=True, ignore=IGNORE)
        (prepared / ".abus-runtime").write_text(fingerprint)
        prepared.rename(runtime_root)


def install(args):
    ensure = getattr(args, "ensure", False)
    home = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    data = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
    target = config / "omarchy/plugins/syndicalt.abus"
    bins, namespace = home / ".local/bin", data / "abus"
    record_path = namespace / "installation.json"
    record = receipt(record_path)
    managed_herdr = getattr(args, "managed_herdr", None)
    if managed_herdr is None:
        managed_herdr = record.get("managed_herdr", True)
    lock = ROOT / "requirements.lock"
    environment = namespace / "environments" / (digest(lock.read_bytes())[:16] + "-py312")
    fingerprint = source_digest(ROOT)
    runtime_root = namespace / "releases" / fingerprint
    files = installation_files(
        target,
        environment / "bin/python",
        config,
        data,
        bins,
        runtime_root,
        managed_herdr=managed_herdr,
    )
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
            fcntl.flock(installation_lock, fcntl.LOCK_EX | (0 if ensure else fcntl.LOCK_NB))
        except BlockingIOError as error:
            raise RuntimeError("Another ABUS installation is in progress") from error
        if receipt(record_path) != record:
            if ensure:
                record = receipt(record_path)
                if getattr(args, "managed_herdr", None) is None:
                    managed_herdr = record.get("managed_herdr", True)
                    files = installation_files(
                        target,
                        environment / "bin/python",
                        config,
                        data,
                        bins,
                        runtime_root,
                        managed_herdr=managed_herdr,
                    )
                for path in files:
                    if not managed(path, record, target, data, bins):
                        raise RuntimeError(f"Installation target changed: {path}")
            else:
                raise RuntimeError("The installation changed; retry from its current state")
        if managed_herdr:
            setup_status("installing")
            prepare_companion(ROOT, artifact=getattr(args, "herdr_artifact", None))
        if (
            ensure
            and record.get("source") == fingerprint
            and (args.no_start or record.get("activated") is True)
            and all(
                path.is_file()
                and digest(path.read_bytes()) == digest(content)
                and path.stat().st_mode & 0o777 == mode
                for path, (content, mode) in files.items()
            )
            and (environment / "bin/python").is_file()
            and (environment / ".abus-environment").is_file()
            and (environment / ".abus-environment").read_text() == digest(lock.read_bytes())
            and (runtime_root / ".abus-runtime").is_file()
            and (runtime_root / ".abus-runtime").read_text() == fingerprint
        ):
            if not args.no_start:
                activate(environment / "bin/python", runtime_root, changed=False, ensure=True)
            setup_status("installed" if args.no_start else "ready")
            print("ABUS installation is current")
            return
        setup_status("installing")
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
            if source_digest(staged, reference=ROOT) != fingerprint or source_digest(ROOT) != fingerprint:
                raise RuntimeError("ABUS source changed during installation; retry from the updated checkout")
            prepare_runtime(staged, runtime_root, fingerprint)
            for path in files:
                if not managed(path, record, target, data, bins):
                    raise RuntimeError(f"Installation target changed while staging: {path}")
            environments = list(dict.fromkeys([str(environment), *record.get("environments", [])]))
            new_record = {
                "version": 1,
                "files": {str(path): digest(content) for path, (content, _) in files.items()},
                "environments": environments,
                "source": fingerprint,
                "activated": False,
                "managed_herdr": managed_herdr,
                "releases": list(dict.fromkeys([str(runtime_root), *record.get("releases", [])])),
            }
            files[record_path] = ((json.dumps(new_record, indent=2) + "\n").encode(), 0o600)
            if target.exists() and (target / ".git").exists() and target.resolve() != ROOT:
                git = target / ".git"
                if git.is_dir():
                    shutil.copytree(git, staged / ".git", symlinks=True)
                else:
                    shutil.copy2(git, staged / ".git")
            publish(files, target, staged if target.resolve() != ROOT else None)
        for directory in (
            home / ".codex/skills",
            home / ".claude/skills",
            home / ".agents/skills",
            home / ".omp/agent/skills",
        ):
            link = directory / "abus"
            directory.mkdir(parents=True, exist_ok=True)
            if not link.exists() and not link.is_symlink():
                link.symlink_to(target / "skill", target_is_directory=True)
        if not args.no_start:
            activate(environment / "bin/python", runtime_root, changed=True, ensure=ensure)
            new_record["activated"] = True
            atomic_write(record_path, (json.dumps(new_record, indent=2) + "\n").encode(), 0o600)
        setup_status("installed" if args.no_start else "ready")
    print(f"Installed ABUS in {target}\nOpen ABUS from your launcher, or run: omarchy abus ui")
    print(f"MCP configuration: {namespace / 'mcp.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-start",
        action="store_true",
        help="Install files without starting services or enabling the widget",
    )
    herdr_options = parser.add_mutually_exclusive_group()
    herdr_options.add_argument(
        "--without-herdr",
        action="store_false",
        dest="managed_herdr",
        default=None,
        help="Install mailbox messaging without the managed Herdr companion",
    )
    herdr_options.add_argument(
        "--with-herdr",
        action="store_true",
        dest="managed_herdr",
        help="Enable managed Herdr provisioning and handoff",
    )
    parser.add_argument(
        "--herdr-artifact",
        type=Path,
        help="Use an offline companion executable (pinned checksum still required)",
    )
    parser.add_argument(
        "--ensure",
        action="store_true",
        help="Provision from plugin activation; keep a current service running without restarting it",
    )
    try:
        install(parser.parse_args())
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ) as error:
        setup_status("failed", str(error)[:1000])
        print(f"ABUS installation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
