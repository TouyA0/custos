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


def _send(command: str) -> bytes | None:
    """Envoie `command` au socket de commandes de Hyprland et renvoie la réponse brute."""
    path = socket_path()
    if path is None:
        return None
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(str(path))
            sock.sendall(command.encode())
            data = b""
            while chunk := sock.recv(65536):
                data += chunk
        return data
    except OSError:
        return None


def request(command: str) -> dict | list | None:
    """Envoie `command` (ex. "cursorpos") au socket de Hyprland et décode la réponse JSON."""
    data = _send(f"j/{command}")
    if data is None:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def send_shortcut(mods: str, key: str, address: str) -> bool:
    """Envoie un raccourci clavier à une fenêtre précise, même si elle n'a pas le focus.

    Syntaxe Lua (config Hyprland en Lua), puis l'ancienne syntaxe en repli.
    """
    lua = f'hl.dsp.send_shortcut({{ mods = "{mods}", key = "{key}", window = "address:{address}" }})'
    for command in (f"dispatch {lua}", f"dispatch sendshortcut {mods}, {key}, address:{address}"):
        if _send(command) == b"ok":
            return True
    return False


def focus_window(address: str) -> bool:
    """Donne le focus à une fenêtre (et affiche son espace de travail)."""
    lua = f'hl.dsp.focus({{ window = "address:{address}" }})'
    return any(
        _send(command) == b"ok"
        for command in (f"dispatch {lua}", f"dispatch focuswindow address:{address}")
    )


def cursor_position() -> tuple[int, int] | None:
    data = request("cursorpos")
    if not isinstance(data, dict):
        return None
    return data["x"], data["y"]


@dataclass(frozen=True)
class Monitor:
    x: int
    y: int
    width: int  # en pixels logiques (taille / échelle), comme les marges layer-shell
    height: int
    name: str = ""  # connecteur : « eDP-1 », « HDMI-A-1 »…
    workspaces: tuple[int, ...] = ()  # espaces de travail affichés (normal et spécial)
    focused: bool = False
    id: int = -1

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def parse_monitor(data: dict) -> Monitor:
    scale = data.get("scale") or 1
    width, height = round(data["width"] / scale), round(data["height"] / scale)
    if data.get("transform", 0) % 2:
        width, height = height, width  # écran tourné d'un quart de tour
    workspaces = tuple(
        workspace_id
        for key in ("activeWorkspace", "specialWorkspace")
        if (workspace_id := (data.get(key) or {}).get("id", 0))
    )
    return Monitor(
        data["x"],
        data["y"],
        width,
        height,
        data.get("name", ""),
        workspaces,
        bool(data.get("focused")),
        data.get("id", -1),
    )


def monitors() -> list[Monitor]:
    """Écrans branchés et allumés."""
    data = request("monitors")
    if not isinstance(data, list):
        return []
    return [parse_monitor(m) for m in data if not m.get("disabled")]


def focused_monitor() -> Monitor | None:
    found = monitors()
    return next((m for m in found if m.focused), found[0] if found else None)


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
    monitor: int = -1  # identifiant de l'écran où Hyprland la range
    stable_id: str = ""  # identifiant pour capturer la fenêtre elle-même (`grim -T`)

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
        monitor=data.get("monitor", -1),
        stable_id=str(data.get("stableId") or ""),
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


def window_of(pids: list[int]) -> ActiveWindow | None:
    """Fenêtre dont le processus est l'un de `pids` (les parents d'un programme en terminal)."""
    clients = request("clients")
    if not isinstance(clients, list):
        return None
    by_pid = {c.get("pid"): c for c in clients if c.get("mapped", True)}
    for pid in pids:  # du plus proche au plus lointain : le terminal avant la session
        if pid in by_pid:
            return parse(by_pid[pid])
    return None


def visible_windows() -> list[ActiveWindow]:
    """Fenêtres affichées en ce moment, de la plus récemment utilisée à la plus ancienne
    (ce qui approche l'ordre d'empilement pour les fenêtres qui se chevauchent)."""
    clients = request("clients")
    if not isinstance(clients, list):
        return []
    workspaces = visible_workspaces()
    shown = [
        c
        for c in clients
        if c.get("mapped", True) and not c.get("hidden") and (c.get("workspace") or {}).get("id") in workspaces
    ]
    shown.sort(key=lambda c: c.get("focusHistoryID", 0))
    return [parse(c) for c in shown]


def visible_workspaces() -> set[int]:
    """Espaces de travail affichés en ce moment (normal et spécial, sur chaque écran)."""
    return {workspace for monitor in monitors() for workspace in monitor.workspaces}


def is_fullscreen(window: dict | None) -> bool:
    """La fenêtre est en vrai plein écran (vidéo, jeu…), pas seulement maximisée."""
    if not window:
        return False
    return int(window.get("fullscreen", 0)) >= FULLSCREEN


def active_is_fullscreen() -> bool:
    data = request("activewindow")
    return is_fullscreen(data if isinstance(data, dict) else None)


def fullscreen_on(monitor_name: str) -> bool:
    """Une fenêtre est en plein écran sur cet écran (l'autre écran peut passer une vidéo
    sans que la mascotte disparaisse)."""
    monitor = next((m for m in monitors() if m.name == monitor_name), None)
    clients = request("clients")
    if monitor is None or not isinstance(clients, list):
        return False
    return any(
        is_fullscreen(c) and (c.get("workspace") or {}).get("id") in monitor.workspaces
        for c in clients
        if c.get("mapped", True)
    )


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
