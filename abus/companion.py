"""Pinned private Herdr executable; never replace the system installation."""

import json
import os
import platform
import tempfile
import urllib.request
from pathlib import Path

from .installation import atomic_write, digest

MAX_BINARY = 192 * 1024 * 1024


def namespace():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "abus/herdr"


def specification(root):
    value = json.loads((root / "integrations/herdr/runtime.json").read_text())
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("protocol") != 22:
        raise RuntimeError("Unsupported Herdr companion manifest")
    return value


def prepare(root, *, artifact=None):
    artifact = artifact or os.environ.get("ABUS_HERDR_ARTIFACT")
    spec = specification(root)
    machine = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine(), platform.machine())
    target = f"{platform.system().lower()}-{machine}"
    entry = spec["artifacts"].get(target)
    if not entry:
        raise RuntimeError(f"No verified Herdr companion artifact for {target}")
    checksum = entry["sha256"]
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise RuntimeError("Invalid Herdr companion checksum")
    directory = namespace()
    if directory.is_symlink():
        raise RuntimeError(f"Refusing a symlink Herdr directory: {directory}")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_uid != os.getuid():
        raise RuntimeError(f"Herdr companion directory is not owned by this user: {directory}")
    destination = directory / checksum
    if destination.is_symlink():
        raise RuntimeError(f"Refusing a symlink Herdr runtime: {destination}")
    destination.mkdir(mode=0o700, exist_ok=True)
    binary = destination / "herdr"
    if binary.is_symlink():
        raise RuntimeError(f"Refusing a symlink Herdr executable: {binary}")
    if not binary.is_file() or digest(binary.read_bytes()) != checksum:
        url = entry["url"]
        if not url.startswith("https://github.com/syndicalt/abus/releases/download/"):
            raise RuntimeError("Herdr companion URL must be a pinned ABUS release asset")
        descriptor, temporary = tempfile.mkstemp(prefix=".download-", dir=destination)
        try:
            with os.fdopen(descriptor, "wb") as output:
                source = Path(artifact).open("rb") if artifact else urllib.request.urlopen(url, timeout=30)
                with source:
                    size = 0
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_BINARY:
                            raise RuntimeError("Herdr companion exceeds the download size limit")
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if digest(Path(temporary).read_bytes()) != checksum:
                raise RuntimeError("Herdr companion checksum verification failed")
            os.chmod(temporary, 0o755)
            os.replace(temporary, binary)
        finally:
            Path(temporary).unlink(missing_ok=True)
    if not os.access(binary, os.X_OK):
        raise RuntimeError(f"Herdr companion is not executable: {binary}")
    for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.txt", "provenance.json"):
        source = root / "integrations/herdr" / name
        if source.is_file():
            atomic_write(destination / name, source.read_bytes(), 0o600)
    record = {
        "version": 1,
        "binary": str(binary),
        "sha256": checksum,
        "runtime_version": spec["runtime_version"],
        "protocol": spec["protocol"],
        "upstream": spec["upstream"],
        "patch_sha256": spec["patch_sha256"],
    }
    atomic_write(directory / "installation.json", (json.dumps(record, indent=2) + "\n").encode(), 0o600)
    return record


def installed():
    """Revalidate executable immediately before a runtime handoff."""
    directory = namespace()
    record_path = directory / "installation.json"
    if directory.is_symlink() or record_path.is_symlink() or not record_path.is_file():
        raise RuntimeError("Managed Herdr companion is not installed; rerun ABUS setup")
    record = json.loads(record_path.read_text())
    if not isinstance(record, dict):
        raise RuntimeError("Invalid installed Herdr companion receipt")
    checksum = record.get("sha256", "")
    if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
        raise RuntimeError("Invalid installed Herdr companion checksum")
    binary = directory / checksum / "herdr"
    if (
        record.get("version") != 1
        or record.get("protocol") != 22
        or record.get("binary") != str(binary)
        or binary.parent.is_symlink()
        or binary.is_symlink()
        or not binary.is_file()
        or not os.access(binary, os.X_OK)
        or digest(binary.read_bytes()) != checksum
    ):
        raise RuntimeError("Installed Herdr companion failed integrity verification; rerun ABUS setup")
    return record
