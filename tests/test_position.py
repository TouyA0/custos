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


WIN = position.Rect(1311, 806, 1228, 773)  # fenêtre foot en bas à droite


def test_attach_from_outside_goes_to_bottom_right_corner() -> None:
    offset = position.attached_offset(100, 100, CANVAS, WIN)
    x, y = position.attached_position(offset, CANVAS, WIN)
    assert (x, y) == (WIN.right - CANVAS - position.ATTACH_MARGIN, WIN.bottom - CANVAS - position.ATTACH_MARGIN)


def test_attach_from_inside_keeps_current_spot() -> None:
    offset = position.attached_offset(1500, 900, CANVAS, WIN)
    assert position.attached_position(offset, CANVAS, WIN) == (1500, 900)


def test_follows_window_move_and_resize() -> None:
    offset = position.attached_offset(1500, 900, CANVAS, WIN)
    moved = position.Rect(200, 100, 1228, 773)
    assert position.attached_position(offset, CANVAS, moved) == (1500 - 1111, 900 - 706)
    # La fenêtre rétrécit un peu : même distance du coin bas-droit…
    smaller = position.Rect(1311, 806, 1100, 773)
    assert position.attached_position(offset, CANVAS, smaller) == (1500 - 128, 900)
    # …beaucoup : ramenée contre le bord gauche plutôt que de sortir de la fenêtre.
    narrow = position.Rect(1311, 806, 600, 773)
    assert position.attached_position(offset, CANVAS, narrow) == (1311, 900)
    # …mais jamais en dehors de la fenêtre.
    tiny = position.Rect(1311, 806, 150, 150)
    x, y = position.attached_position(offset, CANVAS, tiny)
    assert tiny.x <= x <= tiny.right - CANVAS and tiny.y <= y <= tiny.bottom - CANVAS


def test_inside_uses_mascot_center() -> None:
    assert position.inside(WIN.x - CANVAS // 2 + 5, WIN.y, CANVAS, WIN)
    assert not position.inside(WIN.x - CANVAS, WIN.y, CANVAS, WIN)
