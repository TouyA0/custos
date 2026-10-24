"""Capture d'une fenêtre avec `grim`."""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

from custos.config import PrivacyConfig
from custos.context import hyprland, privacy
from custos.context.hyprland import ActiveWindow

log = logging.getLogger(__name__)


def capture(window: ActiveWindow, config: PrivacyConfig | None = None) -> Path | None:
    """Capture la fenêtre et renvoie le chemin du PNG temporaire (à supprimer après usage).

    D'abord la fenêtre elle-même (`grim -T`) : son contenu seul, même recouverte ou sur un
    autre espace de travail. Sinon, la zone de l'écran qu'elle occupe, seulement si c'est
    bien elle qu'on y voit (voir `region_is_safe`) : une fenêtre protégée par-dessus
    partirait à sa place. Le fichier est créé dans XDG_RUNTIME_DIR, lisible par toi seul.
    """
    fd, name = tempfile.mkstemp(prefix="custos-", suffix=".png", dir=os.environ.get("XDG_RUNTIME_DIR"))
    os.close(fd)
    path = Path(name)
    if window.stable_id and _grim(["-T", window.stable_id], path):
        return path
    if region_is_safe(window, config or PrivacyConfig()) and _grim(["-g", window.geometry], path):
        log.info("capture de la zone de la fenêtre (capture de la fenêtre seule impossible)")
        return path
    path.unlink(missing_ok=True)
    return None


def _grim(args: list[str], path: Path) -> bool:
    try:
        subprocess.run(["grim", *args, str(path)], check=True, timeout=5, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def overlaps(a: ActiveWindow, b: ActiveWindow) -> bool:
    return a.x < b.x + b.width and b.x < a.x + a.width and a.y < b.y + b.height and b.y < a.y + a.height


def region_is_safe(window: ActiveWindow, config: PrivacyConfig) -> bool:
    """La zone de la fenêtre montre bien la fenêtre : elle est affichée, et aucune fenêtre
    protégée ne la chevauche."""
    shown = hyprland.visible_windows()
    if not any(w.address == window.address for w in shown):
        return False  # sur un autre espace de travail : la zone montre autre chose
    return not any(
        w.address != window.address
        and overlaps(w, window)
        and privacy.is_blocked(w.window_class, w.title, config)
        for w in shown
    )
