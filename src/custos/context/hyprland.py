"""Infos sur la fenêtre active via `hyprctl activewindow -j`."""

import json
import subprocess
from dataclasses import dataclass


@dataclass
class ActiveWindow:
    window_class: str
    title: str
    x: int
    y: int
    width: int
    height: int

    @property
    def geometry(self) -> str:
        """Géométrie au format attendu par `grim -g`."""
        return f"{self.x},{self.y} {self.width}x{self.height}"


def parse(data: dict) -> ActiveWindow | None:
    if not data.get("at") or not data.get("size"):
        return None
    return ActiveWindow(
        window_class=data.get("class", ""),
        title=data.get("title", ""),
        x=data["at"][0],
        y=data["at"][1],
        width=data["size"][0],
        height=data["size"][1],
    )


def active_window() -> ActiveWindow | None:
    try:
        result = subprocess.run(
            ["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=2, check=False
        )
        return parse(json.loads(result.stdout))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
