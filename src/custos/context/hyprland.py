"""Infos sur la fenêtre active via `hyprctl activewindow -j`."""

from dataclasses import dataclass


@dataclass
class ActiveWindow:
    window_class: str
    title: str
    x: int
    y: int
    width: int
    height: int


def active_window() -> ActiveWindow | None:
    raise NotImplementedError("context.hyprland.active_window")
