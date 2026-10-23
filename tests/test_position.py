from pathlib import Path

import pytest

from custos.ui import position

W, H, CANVAS, BUBBLE = 2560, 1600, 115, 440


@pytest.mark.parametrize(
    ("corner", "expected"),
    [
        ("bottom-right", (W - CANVAS - 24, H - CANVAS - 24)),
        ("top-left", (24, 24)),
        ("top-right", (W - CANVAS - 24, 24)),
        ("bottom-left", (24, H - CANVAS - 24)),
    ],
)
def test_default_position(corner: str, expected: tuple[int, int]) -> None:
    assert position.default_position(corner, 24, CANVAS, W, H) == expected


def test_clamp_keeps_mascot_on_screen() -> None:
    assert position.clamp(-50, 5000, CANVAS, W, H) == (0, H - CANVAS)
    assert position.clamp(100, 200, CANVAS, W, H) == (100, 200)


def test_bubble_above_mascot_in_lower_half() -> None:
    p = position.bubble_placement(2400, 1400, CANVAS, BUBBLE, W, H)
    assert p.edge == "bottom"
    assert p.margin == H - 1400 + position.BUBBLE_GAP  # juste au-dessus de la mascotte
    assert p.left == 2400 + CANVAS - BUBBLE  # alignée sur son bord droit


def test_bubble_below_mascot_in_upper_half() -> None:
    p = position.bubble_placement(1000, 100, CANVAS, BUBBLE, W, H)
    assert p == position.Placement(1000 + CANVAS - BUBBLE, "top", 100 + CANVAS + position.BUBBLE_GAP)


def test_bubble_stays_on_screen_near_left_edge() -> None:
    assert position.bubble_placement(0, 1400, CANVAS, BUBBLE, W, H).left == position.SCREEN_MARGIN


def test_save_and_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert position.load() is None
    position.save(120, 340)
    assert position.load() == (120, 340)
    position.forget()
    assert position.load() is None


def test_corrupt_state_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    (tmp_path / "custos").mkdir()
    (tmp_path / "custos" / "position.json").write_text("{pas du json")
    assert position.load() is None
