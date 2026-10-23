"""Capture de la fenêtre active avec `grim`."""

import os
import subprocess
import tempfile
from pathlib import Path

from custos.context.hyprland import ActiveWindow


def capture(window: ActiveWindow) -> Path | None:
    """Capture la fenêtre et renvoie le chemin du PNG temporaire (à supprimer après usage).

    Le fichier est créé dans XDG_RUNTIME_DIR, en mémoire et lisible par toi seul.
    """
    fd, name = tempfile.mkstemp(prefix="custos-", suffix=".png", dir=os.environ.get("XDG_RUNTIME_DIR"))
    os.close(fd)
    path = Path(name)
    try:
        subprocess.run(["grim", "-g", window.geometry, str(path)], check=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        path.unlink(missing_ok=True)
        return None
    return path
