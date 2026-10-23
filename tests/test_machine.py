from pathlib import Path

import pytest

from custos import machine


def test_distribution(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch\n')
    assert machine.distribution(os_release) == "Arch Linux"
    assert machine.distribution(tmp_path / "absent") == "Linux"


def test_terminal_from_open_command() -> None:
    assert machine.terminal("foot --working-directory={cwd} claude") == "foot"
    assert machine.terminal("/usr/bin/kitty -e claude") == "kitty"
    assert machine.terminal("") == ""


def test_shell_from_foot_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/usr/bin/bash")
    assert machine.shell("foot") == "bash"
    (tmp_path / "foot").mkdir()
    (tmp_path / "foot" / "foot.ini").write_text("font=Mono\nshell=/usr/bin/fish -l\n")
    assert machine.shell("foot") == "fish"
    assert machine.shell("kitty") == "bash"
