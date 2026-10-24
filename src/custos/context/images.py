"""Images que tu joins toi-même : collées dans la carte (Ctrl+V) ou glissées sur la mascotte.

Toujours une copie en PNG dans XDG_RUNTIME_DIR (en mémoire, lisible par toi seul), supprimée
après usage : l'original n'est jamais touché. Une grande photo est réduite : Claude n'en
voit pas plus, et la requête reste légère.
"""

import os
import tempfile
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, GLib

MAX_SIDE = 1568  # pixels : au-delà, Claude réduit l'image de toute façon


def prepare(source: Path) -> Path | None:
    """Copie PNG de `source` (tout format lisible par GdkPixbuf), ou None si ce n'est pas
    une image."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(source))
    except GLib.Error:
        return None
    pixbuf = pixbuf.apply_embedded_orientation() or pixbuf  # photo prise de côté
    width, height = pixbuf.get_width(), pixbuf.get_height()
    scale = min(1.0, MAX_SIDE / max(width, height))
    if scale < 1:
        pixbuf = pixbuf.scale_simple(
            max(1, round(width * scale)), max(1, round(height * scale)), GdkPixbuf.InterpType.BILINEAR
        )
    fd, name = tempfile.mkstemp(prefix="custos-", suffix=".png", dir=os.environ.get("XDG_RUNTIME_DIR"))
    os.close(fd)
    try:
        pixbuf.savev(name, "png", [], [])
    except GLib.Error:
        Path(name).unlink(missing_ok=True)
        return None
    return Path(name)
