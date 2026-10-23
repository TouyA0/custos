"""Capture de la fenêtre active avec `grim`."""

from pathlib import Path


def capture_active_window() -> Path:
    """Capture la fenêtre active et renvoie le chemin du PNG temporaire."""
    raise NotImplementedError("context.screen.capture_active_window")
