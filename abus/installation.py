"""Ownership checks and rollback for user-owned installation files."""

import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

MARKER = "# ABUS managed Omarchy dispatcher"


def setup_status(phase, error=None):
    namespace = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "abus"
    if namespace.is_symlink() or (namespace.exists() and namespace.stat().st_uid != os.getuid()):
        return
    namespace.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(
        namespace / "setup.json",
        (json.dumps({"phase": phase, "error": error, "updated_at": time.time()}) + "\n").encode(),
        0o600,
    )


def source_digest(root, *, reference=None):
    """Fingerprint installed runtime and setup inputs, excluding generated caches."""
    fingerprint = hashlib.sha256()
    reference = reference or root
    paths = [
        reference / name for name in ("manifest.json", "requirements.lock", "install.py", "uninstall.py")
    ]
    for directory in ("abus", "bin", "mcp", "plugin", "ui", "assets", "skill", "integrations/herdr"):
        paths.extend(
            path
            for path in (reference / directory).rglob("*")
            if path.is_file() and "__pycache__" not in path.parts
        )
    for path in sorted(paths):
        relative = path.relative_to(reference)
        fingerprint.update(str(relative).encode() + b"\0" + (root / relative).read_bytes())
    return fingerprint.hexdigest()


def digest(content):
    return hashlib.sha256(content).hexdigest()


def receipt(path):
    if path.is_symlink():
        raise RuntimeError(f"Refusing a symlink installation receipt: {path}")
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("files"), dict):
        raise RuntimeError(f"Invalid ABUS installation receipt: {path}")
    return value


def managed(path, record, plugin, data, bins):
    if path.is_symlink() or (path.exists() and not path.is_file()):
        return False
    if not path.exists():
        return True
    content = path.read_bytes()
    if str(path) in record.get("files", {}):
        return record["files"][str(path)] == digest(content)
    # Recognize the previous installer's exact locations before introducing receipts.
    value = content.decode("utf-8", errors="replace")
    if path == bins / "omarchy":
        return MARKER in value
    if path == bins / "omarchy-abus":
        return (
            value.startswith("#!/bin/sh\nexec ")
            and str(plugin / "bin/omarchy-abus") in value
            and str(data / "abus/venv/bin/python") in value
        )
    if path.name == "abus.service":
        return (
            "Description=ABUS durable agent messaging" in value
            and str(plugin / "plugin/bin/abusd.py") in value
        )
    if path.name == "syndicalt.abus.desktop":
        return "StartupWMClass=syndicalt.abus" in value and str(bins / "omarchy-abus") in value
    if path == data / "abus/mcp.json":
        try:
            entry = json.loads(value)["mcpServers"]["abus"]
            return entry["args"] == [str(plugin / "mcp/abus_mcp.py")] and entry["command"] == str(
                data / "abus/venv/bin/python"
            )
        except (ValueError, KeyError, TypeError):
            return False
    return False


def atomic_write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".abus-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def publish(files, plugin, staged_plugin=None):
    """Publish validated files; a failed filesystem operation restores the prior install."""
    previous = {}
    backup = Path(tempfile.mkdtemp(prefix=".abus-previous-", dir=plugin.parent))
    switched = False
    disposable = False
    try:
        for index, path in enumerate(files):
            previous[path] = (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None
            if previous[path] is not None:
                (backup / str(index)).write_bytes(previous[path][0])
        (backup / "files.json").write_text(json.dumps([str(path) for path in files]))
        if staged_plugin:
            if plugin.exists():
                plugin.rename(backup / "plugin")
            try:
                staged_plugin.rename(plugin)
                switched = True
            except BaseException:
                if (backup / "plugin").exists():
                    (backup / "plugin").rename(plugin)
                raise
        for path, (content, mode) in files.items():
            atomic_write(path, content, mode)
    except BaseException:
        try:
            for path, old in reversed(list(previous.items())):
                if old is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, *old)
            if switched:
                shutil.rmtree(plugin)
                if (backup / "plugin").exists():
                    (backup / "plugin").rename(plugin)
        except BaseException as error:
            raise RuntimeError(
                f"Installation rollback failed; recovery files retained at {backup}"
            ) from error
        disposable = True
        raise
    else:
        disposable = True
    finally:
        if disposable:
            shutil.rmtree(backup)
