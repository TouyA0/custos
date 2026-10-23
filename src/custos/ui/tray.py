"""Icône dans la barre (protocole StatusNotifierItem, sur le bus D-Bus de session).

Avec caelestia : clic gauche = `Activate` (ranger ou afficher la mascotte),
clic droit = `SecondaryActivate` (ouvrir la bulle). Pas de menu D-Bus.
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

INTERFACE = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>
"""


def argb_pixmaps(icon: Path) -> list[tuple[int, int, bytes]]:
    """L'icône à plusieurs tailles, en ARGB32 big-endian comme l'exige le protocole."""
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
        pixmaps.append((width, height, bytes(argb)))
    return pixmaps


class Tray:
    def __init__(self, icon: Path, on_activate: Callable[[], None], on_secondary: Callable[[], None]) -> None:
        self.on_activate = on_activate
        self.on_secondary = on_secondary
        self.pixmaps = argb_pixmaps(icon)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.bus: Gio.DBusConnection | None = None

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
            self.on_activate()
        elif method in ("SecondaryActivate", "ContextMenu"):
            self.on_secondary()
        invocation.return_value(None)

    def _on_get_property(self, _bus, _sender, _path, _interface, name: str) -> GLib.Variant | None:
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "custos"),
            "Title": GLib.Variant("s", "Custos"),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", ""),
            "IconPixmap": GLib.Variant("a(iiay)", self.pixmaps),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], "Custos", TOOLTIP)),
            "ItemIsMenu": GLib.Variant("b", False),
        }
        return values.get(name)
