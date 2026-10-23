"""Position de la mascotte à l'écran et placement de la bulle (sans GTK).

Les coordonnées sont relatives au coin haut-gauche de l'écran, en pixels logiques :
ce sont directement les marges top/left de la surface layer-shell.
"""

import json
from dataclasses import dataclass

from custos.supervisor import state_dir

PADDING = 0.1  # marge autour du sprite dans sa fenêtre (fraction de la taille), pour le flottement
DROP_TOLERANCE = 0.35  # fraction de la mascotte : distance au bord pour s'y accrocher en la lâchant
ATTACH_MARGIN = 12  # écart avec le coin haut-gauche de la fenêtre quand on vient de l'accrocher
# Hauteurs dans les sprites (fraction de leur taille), mesurées sur les images actuelles :
SEAT_LINE = 0.76  # sitting/0 : bas du corps, d'où pendent les jambes
FEET_LINE = 0.96  # sprites debout : dessous des pieds
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


def edge_offset(x: int, canvas: int, window: Rect) -> int:
    """Distance entre le bord gauche de la fenêtre et la mascotte, posée sur son bord haut."""
    return max(0, min(x - window.x, window.width - canvas))


def on_edge(offset: int, size: int, canvas: int, window: Rect, sitting: bool) -> tuple[int, int]:
    """Position de la mascotte sur le bord haut de la fenêtre.

    Assise : la ligne d'assise du sprite sur le bord, jambes dans la fenêtre.
    Debout : les pieds sur le bord.
    """
    line = SEAT_LINE if sitting else FEET_LINE
    x = window.x + max(0, min(offset, window.width - canvas))
    y = window.y - round(size * (PADDING + line))
    return x, y


def near_edge(
    x: int, y: int, size: int, canvas: int, window: Rect, sitting: bool, tolerance: float | None = None
) -> bool:
    """La mascotte est sur le bord haut de la fenêtre (à `tolerance` pixels près)."""
    _, edge_y = on_edge(edge_offset(x, canvas, window), size, canvas, window, sitting)
    edge_y = max(0, edge_y)  # fenêtre collée en haut de l'écran : la mascotte ne peut pas monter plus
    center = x + canvas / 2
    limit = canvas / 2 if tolerance is None else tolerance
    return window.x <= center <= window.right and abs(y - edge_y) <= limit


def edge_target[T](x: int, y: int, size: int, canvas: int, windows: list[tuple[T, Rect]]) -> T | None:
    """Fenêtre dont la mascotte survole le bord haut, pour s'y asseoir quand on la lâche.

    `windows` va de la plus récemment utilisée à la plus ancienne : en cas de
    chevauchement, celle du dessus l'emporte.
    """
    for key, rect in windows:
        if near_edge(x, y, size, canvas, rect, sitting=True, tolerance=canvas * DROP_TOLERANCE):
            return key
    return None


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
