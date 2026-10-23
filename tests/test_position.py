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
SIZE = 96  # canvas = 115 avec la marge de flottement


def test_sits_on_top_edge() -> None:
    x, y = position.on_edge(position.ATTACH_MARGIN, SIZE, CANVAS, WIN, sitting=True)
    assert x == WIN.x + position.ATTACH_MARGIN
    # la ligne d'assise du sprite tombe exactement sur le bord haut de la fenêtre
    assert y + round(SIZE * (position.PADDING + position.SEAT_LINE)) == WIN.y


def test_stands_up_on_the_edge() -> None:
    _, sitting_y = position.on_edge(0, SIZE, CANVAS, WIN, sitting=True)
    _, standing_y = position.on_edge(0, SIZE, CANVAS, WIN, sitting=False)
    assert standing_y < sitting_y  # debout, elle monte : ses pieds se posent sur le bord
    assert standing_y + round(SIZE * (position.PADDING + position.FEET_LINE)) == WIN.y


def test_edge_offset_follows_window_and_stays_inside() -> None:
    offset = position.edge_offset(1500, CANVAS, WIN)
    assert offset == 1500 - WIN.x
    moved = position.Rect(200, 100, 1228, 773)
    assert position.on_edge(offset, SIZE, CANVAS, moved, True)[0] == 200 + offset
    narrow = position.Rect(1311, 806, 150, 773)  # fenêtre rétrécie : reste au-dessus d'elle
    x, _ = position.on_edge(offset, SIZE, CANVAS, narrow, True)
    assert narrow.x <= x <= narrow.right


def test_near_edge() -> None:
    x, y = position.on_edge(200, SIZE, CANVAS, WIN, sitting=True)
    assert position.near_edge(x + 300, y + 10, SIZE, CANVAS, WIN, True)  # glissée le long du bord
    assert not position.near_edge(x, y + 400, SIZE, CANVAS, WIN, True)  # descendue dans la fenêtre
    assert not position.near_edge(100, y, SIZE, CANVAS, WIN, True)  # sortie par la gauche


def test_inside_uses_mascot_center() -> None:
    assert position.inside(WIN.x - CANVAS // 2 + 5, WIN.y, CANVAS, WIN)
    assert not position.inside(WIN.x - CANVAS, WIN.y, CANVAS, WIN)


def test_stowed_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert position.load_stowed() is False
    position.save_stowed(True)
    assert position.load_stowed() is True
    position.save_stowed(False)
    assert position.load_stowed() is False


TOP_LEFT = position.Rect(71, 21, 1228, 773)  # fenêtre collée en haut de l'écran
BOTTOM_LEFT = position.Rect(71, 806, 1228, 773)


def test_drop_on_top_edge_targets_window() -> None:
    windows = [("haut", TOP_LEFT), ("bas", BOTTOM_LEFT)]
    x, y = position.on_edge(400, SIZE, CANVAS, BOTTOM_LEFT, sitting=True)
    assert position.edge_target(x + 5, y + 10, SIZE, CANVAS, windows) == "bas"


def test_drop_in_the_middle_of_a_window_targets_nothing() -> None:
    windows = [("bas", BOTTOM_LEFT)]
    assert position.edge_target(400, 1200, SIZE, CANVAS, windows) is None


def test_window_at_top_of_screen_can_be_targeted() -> None:
    # Son bord est trop haut pour que la mascotte s'assoie dessus : elle est bloquée à y = 0.
    assert position.edge_target(300, 0, SIZE, CANVAS, [("haut", TOP_LEFT)]) == "haut"


def test_most_recent_window_wins_when_overlapping() -> None:
    floating = position.Rect(300, 800, 600, 400)  # fenêtre flottante sur BOTTOM_LEFT
    x, y = position.on_edge(400, SIZE, CANVAS, BOTTOM_LEFT, sitting=True)
    target = position.edge_target(x, y, SIZE, CANVAS, [("flottante", floating), ("bas", BOTTOM_LEFT)])
    assert target == "flottante"
