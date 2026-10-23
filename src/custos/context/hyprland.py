"""Infos Hyprland (fenêtre active, curseur, écran) via son socket de commandes.

Même chose que `hyprctl -j …`, sans lancer de processus : assez rapide pour suivre
la souris pendant qu'on déplace la mascotte.
"""

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path


def socket_path() -> Path | None:
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not signature or not runtime:
        return None
    return Path(runtime) / "hypr" / signature / ".socket.sock"


def request(command: str) -> dict | list | None:
    """Envoie `command` (ex. "cursorpos") au socket de Hyprland et décode la réponse JSON."""
    path = socket_path()
    if path is None:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(str(path))
            sock.sendall(f"j/{command}".encode())
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        return json.loads(data)
    except (OSError, json.JSONDecodeError):
        return None


def cursor_position() -> tuple[int, int] | None:
    data = request("cursorpos")
    if not isinstance(data, dict):
        return None
    return data["x"], data["y"]


@dataclass
class Monitor:
    x: int
    y: int
    width: int  # en pixels logiques (taille / échelle), comme les marges layer-shell
    height: int


def focused_monitor() -> Monitor | None:
    monitors = request("monitors")
    if not isinstance(monitors, list) or not monitors:
        return None
    m = next((m for m in monitors if m.get("focused")), monitors[0])
    scale = m.get("scale") or 1
    return Monitor(m["x"], m["y"], round(m["width"] / scale), round(m["height"] / scale))


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
    data = request("activewindow")
    return parse(data) if isinstance(data, dict) else None
