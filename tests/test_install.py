import argparse
import json
from pathlib import Path

import pytest

import install
import uninstall


def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path / "config/omarchy/plugins/syndicalt.abus"


@pytest.mark.skipif(not Path("/usr/share/omarchy/bin/omarchy").exists(), reason="Omarchy is not installed")
def test_install_and_uninstall_only_manage_their_own_files(tmp_path, monkeypatch):
    plugin = paths(tmp_path, monkeypatch)
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


def test_install_refuses_unrelated_dispatcher_before_modifying_anything(tmp_path, monkeypatch):
    plugin = paths(tmp_path, monkeypatch)
    shim = tmp_path / ".local/bin/omarchy"
    shim.parent.mkdir(parents=True)
    shim.write_text("unrelated wrapper")
    with pytest.raises(RuntimeError, match="not managed"):
        install.install(argparse.Namespace(no_start=True))
    assert shim.read_text() == "unrelated wrapper"
    assert not plugin.exists()
