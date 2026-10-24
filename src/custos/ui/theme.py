"""Couleurs de la carte (sans GTK, testé) : claires, sombres, ou celles de caelestia.

caelestia tire du fond d'écran une palette Material You, rangée dans
~/.local/state/caelestia/scheme.json : la carte la reprend, et suit ses changements.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

AUTO, CAELESTIA, LIGHT, DARK = "auto", "caelestia", "light", "dark"


@dataclass(frozen=True)
class Theme:
    card: str  # fond de la carte
    border: str
    text: str
    accent: str  # bouton « Autoriser »
    on_accent: str
    error: str

    @property
    def code(self) -> str:
        """Fond du code : la couleur du texte, à peine posée sur la carte."""
        return mix(self.card, self.text, 0.1)


LIGHT_THEME = Theme(
    card="#f8f8fa", border="#ffffff", text="#1a1f2b", accent="#1f2d4f", on_accent="#f8f8fa",
    error="#b3402f",
)
DARK_THEME = Theme(
    card="#1c1d24", border="#34363f", text="#e4e4ea", accent="#a9b8e8", on_accent="#1c1d24",
    error="#f08a76",
)


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def rgba(color: str, alpha: float) -> str:
    """« #1a1f2b », 0.5 → « rgba(26, 31, 43, 0.5) » (CSS de GTK)."""
    return "rgba({}, {}, {}, {})".format(*_rgb(color), alpha)


def mix(a: str, b: str, amount: float) -> str:
    """Couleur pleine entre `a` et `b` (0 : a, 1 : b), pour le balisage Pango sans transparence."""
    return "#" + "".join(
        f"{round(x + (y - x) * amount):02x}" for x, y in zip(_rgb(a), _rgb(b), strict=True)
    )


def scheme_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "caelestia" / "scheme.json"


def from_scheme(data: dict) -> Theme | None:
    """Thème à partir de la palette Material You de caelestia, ou None si elle est incomplète."""
    colours = data.get("colours") if isinstance(data, dict) else None
    if not isinstance(colours, dict):
        return None
    keys = {
        "card": "surfaceContainer",
        "border": "outlineVariant",
        "text": "onSurface",
        "accent": "primary",
        "on_accent": "onPrimary",
        "error": "error",
    }
    try:
        values = {field: f"#{colours[key]}" for field, key in keys.items()}
        for value in values.values():
            _rgb(value)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return Theme(**values)


def load(name: str, path: Path | None = None) -> Theme:
    """`light`, `dark`, ou `caelestia` / `auto` : la palette de caelestia si elle est là,
    sinon le thème clair."""
    if name == LIGHT:
        return LIGHT_THEME
    if name == DARK:
        return DARK_THEME
    try:
        theme = from_scheme(json.loads((path or scheme_path()).read_text()))
    except (OSError, ValueError):
        theme = None
    return theme or LIGHT_THEME


def follows_caelestia(name: str) -> bool:
    return name in (AUTO, CAELESTIA)
