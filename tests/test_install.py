import argparse
import json
import subprocess
from pathlib import Path

import pytest

import install
import uninstall
from abus import installation


@pytest.fixture(autouse=True)
def companion_fixture(monkeypatch):
    monkeypatch.setattr(install, "prepare_companion", lambda *_a, **_kw: None)


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
    assert (tmp_path / ".omp/agent/skills/abus").is_symlink()
    record = json.loads((tmp_path / "data/abus/installation.json").read_text())
    runtime = Path(record["releases"][0])
    assert (runtime / ".abus-runtime").is_file()
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
    assert not runtime.exists()
    assert not (tmp_path / ".omp/agent/skills/abus").is_symlink()
    assert unrelated.read_text() == "preserve me"
    assert (state / "abus.db").exists()


@pytest.mark.parametrize(
    "relative",
    [
        ".local/bin/omarchy-abus",
        "config/systemd/user/abus.service",
        "config/systemd/user/abus-cleanup.service",
        "config/systemd/user/abus-cleanup.path",
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


def provision(tmp_path, monkeypatch):
    plugin = paths(tmp_path, monkeypatch)
    executable = tmp_path / "omarchy"
    executable.touch()
    monkeypatch.setattr(install, "OMARCHY", executable)
    monkeypatch.setattr(install.shutil, "which", lambda _name: "/available")
    monkeypatch.setattr(install, "run", lambda *_args: None)
    install.install(argparse.Namespace(no_start=True))
    record = json.loads((tmp_path / "data/abus/installation.json").read_text())
    python = Path(record["environments"][0]) / "bin/python"
    python.parent.mkdir()
    python.touch()
    record["activated"] = True
    (tmp_path / "data/abus/installation.json").write_text(json.dumps(record))
    return plugin, record


def test_shell_reload_keeps_the_current_service_and_runtime(tmp_path, monkeypatch):
    _, record = provision(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(install, "activate", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        install, "publish", lambda *_args: pytest.fail("Current setup must not publish again")
    )
    install.install(argparse.Namespace(ensure=True, no_start=False))
    assert calls == [{"changed": False, "ensure": True}]
    assert json.loads((tmp_path / "data/abus/installation.json").read_text()) == record


def test_upgrade_keeps_old_runtime_available_to_existing_clients(tmp_path, monkeypatch):
    _, record = provision(tmp_path, monkeypatch)
    old = Path(record["releases"][0])
    source = tmp_path / "checkout"
    install.shutil.copytree(install.ROOT, source, ignore=install.IGNORE)
    monkeypatch.setattr(install, "ROOT", source)
    (source / "abus/__init__.py").write_text('"""Updated runtime fixture."""\n')
    calls = []
    monkeypatch.setattr(install, "activate", lambda *args, **kwargs: calls.append(kwargs))
    install.install(argparse.Namespace(ensure=True, no_start=False))
    updated = json.loads((tmp_path / "data/abus/installation.json").read_text())
    assert updated["source"] != record["source"]
    assert Path(updated["releases"][0]) != old
    assert old.is_dir()
    assert len(updated["releases"]) == 2
    assert calls == [{"changed": True, "ensure": True}]


def test_plugin_removal_uses_retained_runtime_to_disable_exposure_and_clean_up(tmp_path, monkeypatch):
    plugin, record = provision(tmp_path, monkeypatch)
    install.shutil.rmtree(plugin)
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout='{"enabled":false}', stderr="")

    monkeypatch.setattr(uninstall.subprocess, "run", run)
    uninstall.uninstall(if_removed=True)
    assert calls[0][-2:] == ["expose", "off"]
    assert ["systemctl", "--user", "disable", "--now", "abus-cleanup.path"] in calls
    assert not any(argv[0] == "omarchy-plugin-disable" for argv in calls)
    assert not any(Path(path).exists() for path in record["releases"])
    assert not (tmp_path / "config/systemd/user/abus.service").exists()


def test_parent_directory_changes_do_not_uninstall_a_present_plugin(tmp_path, monkeypatch):
    plugin, record = provision(tmp_path, monkeypatch)
    monkeypatch.setattr(uninstall.subprocess, "run", lambda *_a, **_k: pytest.fail("Must not stop services"))
    uninstall.uninstall(if_removed=True)
    assert plugin.exists()
    assert Path(record["releases"][0]).exists()


def test_interrupted_activation_is_retried_with_daemon_reload_and_restart(tmp_path, monkeypatch):
    _, record = provision(tmp_path, monkeypatch)
    record["activated"] = False
    receipt_path = tmp_path / "data/abus/installation.json"
    receipt_path.write_text(json.dumps(record))
    calls = []
    monkeypatch.setattr(install, "activate", lambda *args, **kwargs: calls.append(kwargs))
    install.install(argparse.Namespace(ensure=True, no_start=False))
    assert calls == [{"changed": True, "ensure": True}]
    assert json.loads(receipt_path.read_text())["activated"] is True


def test_mailbox_only_choice_survives_automatic_shell_setup(tmp_path, monkeypatch):
    provision(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "prepare_companion", lambda *_a, **_kw: pytest.fail("Companion is disabled"))
    monkeypatch.setattr(install, "activate", lambda *_a, **_kw: None)
    install.install(argparse.Namespace(ensure=True, no_start=False, managed_herdr=False))
    install.install(argparse.Namespace(ensure=True, no_start=False))
    record = json.loads((tmp_path / "data/abus/installation.json").read_text())
    assert record["managed_herdr"] is False
    assert 'Environment="ABUS_HERDR_MANAGED=0"' in (tmp_path / "config/systemd/user/abus.service").read_text()
