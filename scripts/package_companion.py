#!/usr/bin/env python3
"""Prepare verified companion release assets without publishing them."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from abus.installation import digest

ROOT = Path(__file__).resolve().parents[1]


def package(binary, output):
    source = ROOT / "integrations/herdr"
    manifest = json.loads((source / "runtime.json").read_text())
    provenance = json.loads((source / "provenance.json").read_text())
    artifact = manifest["artifacts"]["linux-x86_64"]
    if digest(binary.read_bytes()) != artifact["sha256"]:
        raise RuntimeError("Executable does not match the pinned release artifact")
    if digest((source / "0001-prompt-if-idle.patch").read_bytes()) != manifest["patch_sha256"]:
        raise RuntimeError("Companion patch does not match its manifest")
    if digest((source / "THIRD_PARTY_NOTICES.txt").read_bytes()) != provenance["third_party_notices_sha256"]:
        raise RuntimeError("Companion notices do not match their provenance")
    version = subprocess.run(
        [str(binary), "--version"], check=True, text=True, capture_output=True, timeout=5
    )
    if version.stdout.strip() != "herdr " + manifest["runtime_version"]:
        raise RuntimeError("Executable build identity does not match its manifest")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    files = {
        Path(urlparse(artifact["url"]).path).name: binary,
        **{
            name: source / name
            for name in (
                "runtime.json",
                "provenance.json",
                "0001-prompt-if-idle.patch",
                "LICENSE",
                "THIRD_PARTY_NOTICES.txt",
            )
        },
    }
    for name, path in files.items():
        target = output / name
        if target.exists() and target.read_bytes() != path.read_bytes():
            raise RuntimeError(f"Refusing to overwrite an existing release asset: {target}")
        shutil.copyfile(path, target)
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest((output / name).read_bytes())}  {name}\n" for name in sorted(files))
    )
    print(f"Verified {len(files)} release assets in {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.binary.resolve(), args.output.resolve())
