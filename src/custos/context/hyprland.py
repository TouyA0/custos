"""Infos Hyprland (fenêtre active, curseur, écran) via son socket de commandes.

Même chose que `hyprctl -j …`, sans lancer de processus : assez rapide pour suivre
la souris pendant qu'on déplace la mascotte.
"""

import json
import os
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

FULLSCREEN = 2  # valeur du champ `fullscreen` d'une fenêtre (1 = simplement maximisée)


def socket_path(name: str = ".socket.sock") -> Path | None:
    """Socket de commandes (.socket.sock) ou d'événements (.socket2.sock) de Hyprland."""
    signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not signature or not runtime:
        return None
    return Path(runtime) / "hypr" / signature / name


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
    address: str = ""
    workspace: int = 0

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
        address=data.get("address", ""),
        workspace=(data.get("workspace") or {}).get("id", 0),
    )


def active_window() -> ActiveWindow | None:
    data = request("activewindow")
    return parse(data) if isinstance(data, dict) else None


def find_window(address: str) -> ActiveWindow | None:
    """Fenêtre par son adresse Hyprland, ou None si elle a été fermée."""
    clients = request("clients")
    if not isinstance(clients, list):
        return None
    for client in clients:
        if client.get("address") == address and client.get("mapped", True):
            return parse(client)
    return None


def visible_workspaces() -> set[int]:
    """Espaces de travail affichés en ce moment (normal et spécial, sur chaque écran)."""
    monitors = request("monitors")
    if not isinstance(monitors, list):
        return set()
    ids = set()
    for monitor in monitors:
        for key in ("activeWorkspace", "specialWorkspace"):
            workspace_id = (monitor.get(key) or {}).get("id", 0)
            if workspace_id:
                ids.add(workspace_id)
    return ids


def is_fullscreen(window: dict | None) -> bool:
    """La fenêtre est en vrai plein écran (vidéo, jeu…), pas seulement maximisée."""
    if not window:
        return False
    return int(window.get("fullscreen", 0)) >= FULLSCREEN


def active_is_fullscreen() -> bool:
    data = request("activewindow")
    return is_fullscreen(data if isinstance(data, dict) else None)


def listen(on_event: Callable[[str, str], None], stop: threading.Event) -> None:
    """Lit les événements Hyprland (« nom>>données ») et appelle `on_event(nom, données)`.

    Bloquant : à lancer dans un thread. Se reconnecte si le socket se ferme.
    """
    path = socket_path(".socket2.sock")
    if path is None:
        return
    while not stop.is_set():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(str(path))
                for line in sock.makefile(encoding="utf-8", errors="replace"):
                    name, _, data = line.rstrip("\n").partition(">>")
                    on_event(name, data)
                    if stop.is_set():
                        return
        except OSError:
            pass
        stop.wait(2)  # Hyprland redémarre ? on réessaie un peu plus tard
