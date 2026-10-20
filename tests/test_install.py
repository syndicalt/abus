import argparse
import json
import subprocess
from pathlib import Path

import pytest

import install
import uninstall
from abus import installation


def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "config/omarchy/plugins/syndicalt.abus"


def test_installer_generated_files_and_history_preservation(tmp_path, monkeypatch):
    plugin = paths(tmp_path, monkeypatch)
    executable = tmp_path / "omarchy"
    executable.touch()
    monkeypatch.setattr(install, "OMARCHY", executable)
    monkeypatch.setattr(install.shutil, "which", lambda _name: "/available")
    calls = []
    monkeypatch.setattr(install, "run", lambda *args: calls.append(args))
    install.install(argparse.Namespace(no_start=True))
    assert (plugin / "ui/shell.qml").exists()
    assert not (plugin / ".venv").exists()
    assert "ABUS managed" in (tmp_path / ".local/bin/omarchy").read_text()
    assert (tmp_path / ".codex/skills/abus").is_symlink()
    mcp = json.loads((tmp_path / "data/abus/mcp.json").read_text())["mcpServers"]["abus"]
    assert Path(mcp["env"]["XDG_RUNTIME_DIR"]).is_absolute()
    assert any(c[0] == "omarchy-plugin-validate" for c in calls)
    assert not any(c[0] == "systemctl" for c in calls)
    unrelated = tmp_path / "config/systemd/user/unrelated.service"
    unrelated.write_text("preserve me")
    state = tmp_path / "state/omarchy/abus"
    state.mkdir(parents=True)
    (state / "abus.db").write_text("history placeholder")
    uninstall.uninstall(files_only=True)
    assert not plugin.exists()
    assert not (tmp_path / ".local/bin/omarchy").exists()
    assert unrelated.read_text() == "preserve me"
    assert (state / "abus.db").exists()


@pytest.mark.parametrize(
    "relative",
    [
        ".local/bin/omarchy-abus",
        "config/systemd/user/abus.service",
        "data/applications/syndicalt.abus.desktop",
        "data/abus/mcp.json",
    ],
)
def test_every_unrelated_output_is_refused_before_copying(tmp_path, monkeypatch, relative):
    plugin = paths(tmp_path, monkeypatch)
    unrelated = tmp_path / relative
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("unrelated content")
    with pytest.raises(RuntimeError, match="not managed"):
        install.install(argparse.Namespace(no_start=True))
    assert unrelated.read_text() == "unrelated content"
    assert not plugin.exists()


def test_partial_publish_restores_code_and_generated_files(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "code").write_text("old code")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "code").write_text("new code")
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_text("old first")
    second.write_text("old second")
    write = installation.atomic_write
    failed = False

    def injected_failure(path, content, mode):
        nonlocal failed
        if path == second and not failed:
            failed = True
            raise OSError("controlled publication failure")
        write(path, content, mode)

    monkeypatch.setattr(installation, "atomic_write", injected_failure)
    with pytest.raises(OSError, match="publication failure"):
        installation.publish({first: (b"new first", 0o600), second: (b"new second", 0o600)}, plugin, staged)
    assert (plugin / "code").read_text() == "old code"
    assert first.read_text() == "old first"
    assert second.read_text() == "old second"
    assert not list(tmp_path.glob(".abus-previous-*"))


def test_dependency_provisioning_failure_does_not_publish_files(tmp_path, monkeypatch):
    plugin = paths(tmp_path, monkeypatch)
    executable = tmp_path / "omarchy"
    executable.touch()
    monkeypatch.setattr(install, "OMARCHY", executable)
    monkeypatch.setattr(install.shutil, "which", lambda _name: "/available")

    def run(*args):
        if args[:3] == ("uv", "pip", "sync"):
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(install, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        install.install(argparse.Namespace(no_start=True))
    assert not plugin.exists()
    assert not (tmp_path / ".local/bin/omarchy").exists()
    assert not list((tmp_path / "data/abus/environments").iterdir())


def test_uninstall_keeps_installation_when_exposure_cannot_be_verified(tmp_path, monkeypatch):
    plugin = paths(tmp_path, monkeypatch)
    executable = tmp_path / "omarchy"
    executable.touch()
    monkeypatch.setattr(install, "OMARCHY", executable)
    monkeypatch.setattr(install.shutil, "which", lambda _name: "/available")
    monkeypatch.setattr(install, "run", lambda *_args: None)
    install.install(argparse.Namespace(no_start=True))
    calls = []

    def fail(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="inspection unavailable")

    monkeypatch.setattr(uninstall.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="installation retained"):
        uninstall.uninstall()
    assert plugin.exists()
    assert (tmp_path / ".local/bin/omarchy-abus").exists()
    assert len(calls) == 1


def test_uninstall_refuses_modified_owned_files(tmp_path, monkeypatch):
    paths(tmp_path, monkeypatch)
    executable = tmp_path / "omarchy"
    executable.touch()
    monkeypatch.setattr(install, "OMARCHY", executable)
    monkeypatch.setattr(install.shutil, "which", lambda _name: "/available")
    monkeypatch.setattr(install, "run", lambda *_args: None)
    install.install(argparse.Namespace(no_start=True))
    cli = tmp_path / ".local/bin/omarchy-abus"
    cli.write_text("user's replacement")
    with pytest.raises(RuntimeError, match="modified"):
        uninstall.uninstall(files_only=True)
    assert cli.read_text() == "user's replacement"


def test_install_refuses_unrelated_dispatcher_before_modifying_anything(tmp_path, monkeypatch):
    plugin = paths(tmp_path, monkeypatch)
    shim = tmp_path / ".local/bin/omarchy"
    shim.parent.mkdir(parents=True)
    shim.write_text("unrelated wrapper")
    with pytest.raises(RuntimeError, match="not managed"):
        install.install(argparse.Namespace(no_start=True))
    assert shim.read_text() == "unrelated wrapper"
    assert not plugin.exists()
