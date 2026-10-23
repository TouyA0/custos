"""Texte sélectionné (`wl-paste --primary`) et presse-papier (`wl-paste`)."""

import subprocess


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
