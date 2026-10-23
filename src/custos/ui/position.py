"""Position de la mascotte à l'écran et placement de la bulle (sans GTK).

Les coordonnées sont relatives au coin haut-gauche de l'écran, en pixels logiques :
ce sont directement les marges top/left de la surface layer-shell.
"""

import json
from dataclasses import dataclass

from custos.supervisor import state_dir

ATTACH_MARGIN = 12  # écart avec le coin de la fenêtre quand on vient de l'accrocher
BUBBLE_GAP = 4  # écart entre la mascotte et la bulle
SCREEN_MARGIN = 8  # la bulle ne colle jamais au bord de l'écran


@dataclass(frozen=True)
class Placement:
    """Où poser la bulle : bord vertical d'ancrage ("top" ou "bottom") et marges."""

    left: int
    edge: str
    margin: int


def default_position(position: str, margin: int, canvas: int, width: int, height: int) -> tuple[int, int]:
    """Position initiale à partir de la config (`bottom-right`…) quand rien n'est mémorisé."""
    vertical, horizontal = position.split("-")
    x = margin if horizontal == "left" else width - canvas - margin
    y = margin if vertical == "top" else height - canvas - margin
    return x, y


def clamp(x: int, y: int, canvas: int, width: int, height: int) -> tuple[int, int]:
    """Garde la mascotte entièrement visible."""
    return max(0, min(x, width - canvas)), max(0, min(y, height - canvas))


def bubble_placement(x: int, y: int, canvas: int, bubble_width: int, width: int, height: int) -> Placement:
    """Bulle alignée sur le bord droit de la mascotte, du côté où il y a le plus de place.

    Ancrée par le bas quand elle est au-dessus de la mascotte : elle grandit alors
    vers le haut, sans jamais la recouvrir.
    """
    left = x + canvas - bubble_width
    left = max(SCREEN_MARGIN, min(left, width - bubble_width - SCREEN_MARGIN))
    if y + canvas / 2 > height / 2:
        return Placement(left, "bottom", height - y + BUBBLE_GAP)
    return Placement(left, "top", y + canvas + BUBBLE_GAP)


@dataclass(frozen=True)
class Rect:
    """Fenêtre, en coordonnées de l'écran (comme la mascotte)."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height


def inside(x: int, y: int, canvas: int, window: Rect) -> bool:
    """Le centre de la mascotte est dans la fenêtre."""
    cx, cy = x + canvas / 2, y + canvas / 2
    return window.x <= cx <= window.right and window.y <= cy <= window.bottom


def attached_offset(x: int, y: int, canvas: int, window: Rect) -> tuple[int, int]:
    """Écart entre la mascotte et le coin bas-droit de la fenêtre, à conserver.

    Mesuré depuis ce coin pour qu'elle y reste collée quand la fenêtre change de
    taille. Si elle n'est pas sur la fenêtre, elle va se poser dans ce coin.
    """
    if inside(x, y, canvas, window):
        return window.right - x, window.bottom - y
    return canvas + ATTACH_MARGIN, canvas + ATTACH_MARGIN


def attached_position(offset: tuple[int, int], canvas: int, window: Rect) -> tuple[int, int]:
    """Position de la mascotte accrochée, toujours dans la fenêtre."""
    x = window.right - offset[0]
    y = window.bottom - offset[1]
    x = max(window.x, min(x, window.right - canvas))
    y = max(window.y, min(y, window.bottom - canvas))
    return x, y


def _state_file():
    return state_dir() / "position.json"


def load() -> tuple[int, int] | None:
    try:
        data = json.loads(_state_file().read_text())
        return int(data["x"]), int(data["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(x: int, y: int) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"x": x, "y": y}))


def forget() -> None:
    _state_file().unlink(missing_ok=True)


def _stowed_file():
    return state_dir() / "stowed"


def load_stowed() -> bool:
    """La mascotte était rangée dans la barre à la dernière session."""
    return _stowed_file().exists()


def save_stowed(stowed: bool) -> None:
    path = _stowed_file()
    if stowed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    else:
        path.unlink(missing_ok=True)
