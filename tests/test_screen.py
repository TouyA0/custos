import subprocess
from pathlib import Path

import pytest

from custos.config import PrivacyConfig
from custos.context import screen
from custos.context.hyprland import ActiveWindow

WINDOW = ActiveWindow("foot", "~/code", x=0, y=0, width=800, height=600, address="0x1", stable_id="18000004")
KEEPASS = ActiveWindow("org.keepassxc.KeePassXC", "Coffre", x=500, y=400, width=400, height=300, address="0x2")
ELSEWHERE = ActiveWindow("org.keepassxc.KeePassXC", "Coffre", x=900, y=0, width=400, height=300, address="0x3")
PRIVACY = PrivacyConfig(blocked_classes=["org.keepassxc.KeePassXC"])


@pytest.fixture
def grim(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    """Remplace grim (`-T` réussit ou non) et la liste des fenêtres affichées."""
    state: dict = {"calls": [], "toplevel": True, "shown": [WINDOW]}

    def run(argv, **_kwargs):
        state["calls"].append(argv[1:-1])
        if argv[1] == "-T" and not state["toplevel"]:
            raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(screen.subprocess, "run", run)
    monkeypatch.setattr(screen.hyprland, "visible_windows", lambda: state["shown"])
    return state


def test_captures_the_window_itself(grim: dict) -> None:
    grim["shown"] = [KEEPASS, WINDOW]  # recouverte : sans importance pour `grim -T`
    assert screen.capture(WINDOW, PRIVACY)
    assert grim["calls"] == [["-T", "18000004"]]


def test_falls_back_to_region_when_safe(grim: dict) -> None:
    grim["toplevel"] = False
    grim["shown"] = [ELSEWHERE, WINDOW]  # protégée, mais à côté
    assert screen.capture(WINDOW, PRIVACY)
    assert grim["calls"][-1] == ["-g", "0,0 800x600"]


def test_no_region_capture_under_a_blocked_window(grim: dict, tmp_path: Path) -> None:
    grim["toplevel"] = False
    grim["shown"] = [KEEPASS, WINDOW]
    assert screen.capture(WINDOW, PRIVACY) is None
    assert ["-g", "0,0 800x600"] not in grim["calls"]
    assert list(tmp_path.glob("custos-*")) == []  # fichier temporaire supprimé


def test_no_region_capture_on_another_workspace(grim: dict) -> None:
    grim["toplevel"] = False
    grim["shown"] = []
    assert screen.capture(WINDOW, PRIVACY) is None
    assert len(grim["calls"]) == 1
