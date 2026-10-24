"""Icône dans la barre (protocole StatusNotifierItem, sur le bus D-Bus de session).

Avec caelestia : clic gauche = `Activate` (ranger ou afficher la mascotte),
clic droit = `SecondaryActivate` (ouvrir la bulle). Pas de menu D-Bus.

Quand quelque chose t'attend (Custos ou une session Claude Code veut ton accord, ou a
fini), l'icône prend un point de couleur et son info-bulle dit pourquoi : utile quand la
mascotte est rangée, cachée par un plein écran ou sur l'autre écran. Le clic gauche y mène
alors, au lieu de ranger la mascotte.

Chaque état a son propre nom d'icône (fichiers PNG dans ICON_DIR, annoncé par
IconThemePath) : la barre de caelestia (quickshell) garde l'image en cache tant que son
adresse ne change pas, même après le signal NewIcon. IconPixmap reste là pour les autres.
Toutes les méthodes s'exécutent dans le thread GTK.
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, Gio, GLib

log = logging.getLogger(__name__)

WATCHER = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
ICON_SIZES = (22, 32, 48, 64)
TOOLTIP = "Clic : ranger ou afficher la mascotte · clic droit : lui poser une question"
WAITING, DONE = "waiting", "done"  # alertes : il attend ton accord, ou il a fini
DOT_COLORS = {WAITING: (232, 116, 59), DONE: (76, 175, 80)}  # orange, vert (RGB)
DOT_RADIUS = 0.22  # fraction de la taille de l'icône
ICON_NAMES = {None: "custos-tray", WAITING: "custos-tray-waiting", DONE: "custos-tray-done"}
FILE_SIZE = 64  # pixels des fichiers PNG

INTERFACE = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""


def add_dot(argb: bytearray, size: int, color: tuple[int, int, int]) -> None:
    """Pastille ronde en haut à droite, cerclée de blanc pour se voir sur toute barre."""
    radius = max(3.0, size * DOT_RADIUS)
    cx, cy = size - radius - 0.5, radius + 0.5
    for y in range(size):
        for x in range(size):
            distance = ((x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2) ** 0.5
            if distance > radius:
                continue
            r, g, b = color if distance <= radius - max(1.0, size / 22) else (255, 255, 255)
            argb[(y * size + x) * 4 : (y * size + x) * 4 + 4] = bytes((255, r, g, b))


def icon_dir() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "custos-tray"


def write_icon_files(icon: Path, folder: Path) -> None:
    """Un PNG par état. Sans extension aussi : caelestia cherche `<dossier>/<nom>` tel quel."""
    folder.mkdir(parents=True, exist_ok=True)
    for dot, name in ICON_NAMES.items():
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon), FILE_SIZE, FILE_SIZE, True)
        if not pixbuf.get_has_alpha():
            pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
        width, height, stride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
        pixels = pixbuf.get_pixels()
        rgba = bytearray(b"".join(pixels[y * stride : y * stride + width * 4] for y in range(height)))
        if dot and width == height:
            argb = bytearray(width * height * 4)
            add_dot(argb, width, DOT_COLORS[dot])
            for i in range(0, len(argb), 4):
                if argb[i]:  # pixel de la pastille : A, R, G, B → R, G, B, A
                    rgba[i : i + 4] = bytes((argb[i + 1], argb[i + 2], argb[i + 3], argb[i]))
        image = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(bytes(rgba)), GdkPixbuf.Colorspace.RGB, True, 8, width, height, width * 4
        )
        for path in (folder / f"{name}.png", folder / name):
            image.savev(str(path), "png", [], [])


def argb_pixmaps(icon: Path, dot: str | None = None) -> list[tuple[int, int, bytes]]:
    """L'icône à plusieurs tailles, en ARGB32 big-endian comme l'exige le protocole ;
    `dot` (WAITING, DONE) y ajoute la pastille de couleur."""
    pixmaps = []
    for size in ICON_SIZES:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(icon), size, size, True)
        if not pixbuf.get_has_alpha():
            pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
        width, height, stride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
        rgba = pixbuf.get_pixels()
        argb = bytearray(width * height * 4)
        for y in range(height):
            row = rgba[y * stride : y * stride + width * 4]
            for x in range(width):
                r, g, b, a = row[x * 4 : x * 4 + 4]
                argb[(y * width + x) * 4 : (y * width + x) * 4 + 4] = bytes((a, r, g, b))
        if dot and width == height:
            add_dot(argb, width, DOT_COLORS[dot])
        pixmaps.append((width, height, bytes(argb)))
    return pixmaps


class Tray:
    def __init__(self, icon: Path, on_activate: Callable[[], None], on_secondary: Callable[[], None]) -> None:
        self.on_activate = on_activate
        self.on_secondary = on_secondary
        self.icons = {None: argb_pixmaps(icon)} | {kind: argb_pixmaps(icon, kind) for kind in DOT_COLORS}
        self.icon_dir = icon_dir()
        try:
            write_icon_files(icon, self.icon_dir)
        except (OSError, GLib.Error) as exc:
            log.warning("icônes de la barre non écrites (%s) : pas de pastille dans caelestia", exc)
            self.icon_dir = None
        self.alert: str | None = None  # WAITING, DONE ou None
        self.alert_text = ""
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.bus: Gio.DBusConnection | None = None

    def set_alert(self, alert: str | None, text: str = "") -> None:
        """Point de couleur et info-bulle : ce qui t'attend (None : rien)."""
        if (alert, text) == (self.alert, self.alert_text):
            return
        self.alert, self.alert_text = alert, text
        if self.bus is None:
            return
        for signal, params in (
            ("NewIcon", None),
            ("NewAttentionIcon", None),
            ("NewToolTip", None),
            ("NewStatus", GLib.Variant("(s)", (self._status(),))),
        ):
            try:
                self.bus.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", signal, params)
            except GLib.Error as exc:
                log.warning("impossible de mettre à jour l'icône : %s", exc.message)
                return

    def _status(self) -> str:
        return "NeedsAttention" if self.alert else "Active"

    def start(self) -> None:
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        except GLib.Error as exc:
            log.warning("pas de bus D-Bus de session : pas d'icône dans la barre (%s)", exc.message)
            return
        interface = Gio.DBusNodeInfo.new_for_xml(INTERFACE).interfaces[0]
        self.bus.register_object(ITEM_PATH, interface, self._on_call, self._on_get_property, None)
        Gio.bus_own_name_on_connection(
            self.bus, self.name, Gio.BusNameOwnerFlags.NONE, None, None
        )
        # La barre peut démarrer après nous, ou redémarrer : on s'annonce à chaque apparition.
        Gio.bus_watch_name_on_connection(
            self.bus, WATCHER, Gio.BusNameWatcherFlags.NONE, self._register, None
        )

    def _register(self, bus: Gio.DBusConnection, *_args) -> None:
        bus.call(
            WATCHER,
            "/StatusNotifierWatcher",
            WATCHER,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self.name,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_registered,
        )

    def _on_registered(self, bus: Gio.DBusConnection, result: Gio.AsyncResult) -> None:
        try:
            bus.call_finish(result)
            log.info("icône ajoutée à la barre")
        except GLib.Error as exc:
            log.warning("la barre a refusé l'icône : %s", exc.message)

    def _on_call(self, _bus, _sender, _path, _interface, method: str, _params, invocation) -> None:
        if method == "Activate":
            # Quelque chose t'attend : le clic y mène (comme sur la mascotte).
            (self.on_secondary if self.alert else self.on_activate)()
        elif method in ("SecondaryActivate", "ContextMenu"):
            self.on_secondary()
        invocation.return_value(None)

    def _on_get_property(self, _bus, _sender, _path, _interface, name: str) -> GLib.Variant | None:
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "custos"),
            "Title": GLib.Variant("s", "Custos"),
            "Status": GLib.Variant("s", self._status()),
            "IconName": GLib.Variant("s", ICON_NAMES[self.alert] if self.icon_dir else ""),
            # En « NeedsAttention », la barre affiche l'icône d'attention : la même.
            "AttentionIconName": GLib.Variant("s", ICON_NAMES[self.alert] if self.icon_dir else ""),
            "IconThemePath": GLib.Variant("s", str(self.icon_dir or "")),
            # Avec les fichiers, le nom suffit : une image ferait ignorer le nom par caelestia.
            "IconPixmap": GLib.Variant("a(iiay)", [] if self.icon_dir else self.icons[self.alert]),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", [] if self.icon_dir else self.icons[self.alert]),
            "ToolTip": GLib.Variant(
                "(sa(iiay)ss)", ("", [], "Custos", self.alert_text or TOOLTIP)
            ),
            "ItemIsMenu": GLib.Variant("b", False),
        }
        return values.get(name)
