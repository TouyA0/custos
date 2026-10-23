"""Texte sélectionné (`wl-paste --primary`) et presse-papier (`wl-paste`)."""

import os
import subprocess
import tempfile
from pathlib import Path


def _paste(*args: str) -> str:
    # wl-paste sort avec le code 1 quand rien n'est copié : on renvoie alors "".
    try:
        result = subprocess.run(
            ["wl-paste", "--no-newline", "--type", "text", *args],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout if result.returncode == 0 else ""


def selection() -> str:
    return _paste("--primary")


def clipboard() -> str:
    return _paste()


def copy(text: str) -> bool:
    try:
        subprocess.run(["wl-copy", "--", text], timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def image_to_file() -> Path | None:
    """Enregistre l'image du presse-papier dans un PNG temporaire (à supprimer après usage)."""
    fd, name = tempfile.mkstemp(prefix="custos-", suffix=".png", dir=os.environ.get("XDG_RUNTIME_DIR"))
    path = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            subprocess.run(["wl-paste", "--type", "image/png"], stdout=f, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        path.unlink(missing_ok=True)
        return None
    if path.stat().st_size == 0:
        path.unlink()
        return None
    return path
