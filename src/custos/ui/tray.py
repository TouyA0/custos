"""Icône dans la barre (protocole StatusNotifierItem, sur le bus D-Bus de session).

Avec caelestia : clic gauche = `Activate` (ranger ou afficher la mascotte),
clic droit = `SecondaryActivate` (ouvrir la bulle). Au survol, caelestia affiche le menu
(protocole DBusMenu, voir Menu) : poser une question, conversations récentes, sessions
Claude Code, ranger la mascotte, quitter.

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
from collections.abc import Callable, Sequence
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, Gio, GLib

log = logging.getLogger(__name__)

WATCHER = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
MENU_INTERFACE_NAME = "com.canonical.dbusmenu"
ICON_SIZES = (22, 32, 48, 64)
TOOLTIP = "Clic : ranger ou afficher la mascotte · clic droit : lui poser une question"
WAITING, DONE, PROBLEM = "waiting", "done", "problem"  # attend ton accord, a fini, à regarder
DOT_COLORS = {WAITING: (232, 116, 59), DONE: (76, 175, 80), PROBLEM: (217, 163, 58)}  # orange, vert, ambre
DOT_RADIUS = 0.22  # fraction de la taille de l'icône
ICON_NAMES = {
    None: "custos-tray", WAITING: "custos-tray-waiting", DONE: "custos-tray-done", PROBLEM: "custos-tray-problem",
}
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
    <property name="Menu" type="o" access="read"/>
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


MENU_INTERFACE = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
  </interface>
</node>
"""

# Entrées du menu : (texte, action) où l'action est une fonction, une liste d'entrées
# (sous-menu) ou None (titre de section, grisé) ; None seul : un séparateur. Un troisième
# élément, (icône, couleur), ajoute une icône (voir icons.py) : (texte, action, ("check", "#4caf50")).
MenuEntry = tuple | None
MENU_ICON_SIZE = 32  # pixels de l'image envoyée à la barre


class Menu:
    """Le menu de l'icône (DBusMenu), reconstruit à chaque ouverture à partir de `entries()`.

    Chaque entrée a un numéro (0 : la racine) ; la révision augmente quand le menu change,
    pour que la barre le redemande.
    """

    def __init__(self, entries: Callable[[], Sequence[MenuEntry]]) -> None:
        self.entries = entries
        self.revision = 0
        self.items: dict[int, tuple[dict, list[int]]] = {0: ({"children-display": "submenu"}, [])}
        self.actions: dict[int, Callable[[], None]] = {}
        self._shape: list | None = None

    def rebuild(self) -> bool:
        """Relit les entrées ; True si le menu a changé (nouvelle révision)."""
        try:
            entries = list(self.entries())
        except Exception:
            log.exception("menu de l'icône impossible à construire")
            return False
        items: dict[int, tuple[dict, list[int]]] = {}
        actions: dict[int, Callable[[], None]] = {}
        shape: list = []

        def add(level: Sequence[MenuEntry | None], parent: list[int]) -> None:
            for entry in level:
                number = len(items) + 1
                if entry is None:
                    items[number] = ({"type": "separator"}, [])
                    shape.append(None)
                else:
                    label, action, *icon = entry
                    # « _ » souligne la lettre suivante (raccourci) : on le double.
                    props: dict = {"label": label.replace("_", "__")}
                    if icon:
                        props["icon-data"] = _icon_png(*icon[0])
                        shape.append(icon[0])
                    children: list[int] = []
                    items[number] = (props, children)
                    shape.append(label)
                    if callable(action):
                        actions[number] = action
                    elif action is None:
                        props["enabled"] = False
                    else:
                        props["children-display"] = "submenu"
                        shape.append("[")
                        add(action, children)
                        shape.append("]")
                parent.append(number)

        root: list[int] = []
        add(entries, root)
        items[0] = ({"children-display": "submenu"}, root)
        self.items, self.actions = items, actions
        if shape == self._shape:
            return False
        self._shape = shape
        self.revision += 1
        return True

    def layout(self, number: int = 0, depth: int = -1) -> tuple:
        """(id, propriétés, enfants) au format DBusMenu, `depth` niveaux (-1 : tous). Les
        enfants sont des variants ; l'entrée elle-même, un tuple à placer dans la réponse."""
        props, children = self.items.get(number, ({}, []))
        kids = [
            GLib.Variant("(ia{sv}av)", self.layout(child, depth - 1))
            for child in ([] if depth == 0 else children)
        ]
        return number, _props(props), kids

    def activate(self, number: int) -> bool:
        action = self.actions.get(number)
        if action is None:
            return False
        try:
            action()
        except Exception:
            log.exception("action du menu en erreur")
        return True


_icon_cache: dict[tuple[str, str], bytes] = {}


def _icon_png(name: str, color: str) -> bytes:
    key = (name, color)
    if key not in _icon_cache:
        from custos.ui import icons

        _icon_cache[key] = icons.png(name, color, MENU_ICON_SIZE)
    return _icon_cache[key]


def _variant(value) -> GLib.Variant:
    if isinstance(value, bool):
        return GLib.Variant("b", value)
    if isinstance(value, bytes):
        return GLib.Variant("ay", value)
    return GLib.Variant("s", value)


def _props(props: dict) -> dict[str, GLib.Variant]:
    return {key: _variant(value) for key, value in props.items()}


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
    def __init__(
        self,
        icon: Path,
        on_activate: Callable[[], None],
        on_secondary: Callable[[], None],
        menu: Callable[[], Sequence[MenuEntry]] = list,
    ) -> None:
        self.on_activate = on_activate
        self.on_secondary = on_secondary
        self.menu = Menu(menu)
        self.icons = {None: argb_pixmaps(icon)} | {kind: argb_pixmaps(icon, kind) for kind in DOT_COLORS}
        self.icon_dir = icon_dir()
        try:
            write_icon_files(icon, self.icon_dir)
        except (OSError, GLib.Error) as exc:
            log.warning("icônes de la barre non écrites (%s) : pas de pastille dans caelestia", exc)
            self.icon_dir = None
        self.quota = None  # dernier quota connu (voir quota.Quota), dans l'info-bulle
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
        self.refresh_menu()  # une session Claude Code a changé d'état
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

    def set_quota(self, quota) -> None:
        """Nouveau relevé du quota : en haut du menu (et dans l'info-bulle, pour les barres
        qui l'affichent)."""
        self.quota = quota
        if self.bus is None:
            return
        self.refresh_menu()
        try:
            self.bus.emit_signal(None, ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)
        except GLib.Error as exc:
            log.warning("impossible de mettre à jour l'info-bulle : %s", exc.message)

    def tooltip(self) -> str:
        """Ce qui t'attend (ou à quoi servent les clics), puis le quota, recalculé à chaque
        survol : « jusqu'à 17:30 » devient « remis à zéro » une fois l'heure passée."""
        text = self.alert_text or TOOLTIP
        usage = self.quota.describe() if self.quota else ""
        return f"{text}\n{usage}" if usage else text

    def refresh_menu(self) -> None:
        """Reconstruit le menu et prévient la barre s'il a changé."""
        if self.bus is None or not self.menu.rebuild():
            return
        try:
            self.bus.emit_signal(
                None, MENU_PATH, MENU_INTERFACE_NAME, "LayoutUpdated",
                GLib.Variant("(ui)", (self.menu.revision, 0)),
            )
        except GLib.Error as exc:
            log.warning("impossible de mettre à jour le menu : %s", exc.message)

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
        self.menu.rebuild()
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_INTERFACE).interfaces[0]
        self.bus.register_object(MENU_PATH, menu, self._on_menu_call, self._on_menu_property, None)
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
                "(sa(iiay)ss)", ("", [], "Custos", self.tooltip())
            ),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(name)

    # --- menu (DBusMenu) ---------------------------------------------------------

    def _on_menu_call(self, _bus, _sender, _path, _interface, method: str, params, invocation) -> None:
        try:
            self._answer_menu_call(method, params, invocation)
        except Exception as exc:
            # Toujours une réponse : sinon la barre attend jusqu'à son délai.
            log.exception("menu de l'icône : %s en erreur", method)
            invocation.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(exc))

    def _answer_menu_call(self, method: str, params, invocation) -> None:
        menu = self.menu
        args = params.unpack()
        if method == "GetLayout":
            parent, depth, _names = args
            if parent == 0:
                menu.rebuild()
            result = GLib.Variant("(u(ia{sv}av))", (menu.revision, menu.layout(parent, depth)))
        elif method == "GetGroupProperties":
            ids, _names = args
            wanted = ids or list(menu.items)
            result = GLib.Variant(
                "(a(ia{sv}))", ([(i, _props(menu.items[i][0])) for i in wanted if i in menu.items],)
            )
        elif method == "GetProperty":
            number, name = args
            value = menu.items.get(number, ({}, []))[0].get(name, "")
            result = GLib.Variant("(v)", (_props({name: value})[name],))
        elif method == "Event":
            number, event, _data, _time = args
            if event == "clicked":
                self._activate_later(number)
            result = None
        elif method == "EventGroup":
            errors = []
            for number, event, _data, _time in args[0]:
                if number not in menu.items:
                    errors.append(number)
                elif event == "clicked":
                    self._activate_later(number)
            result = GLib.Variant("(ai)", (errors,))
        elif method == "AboutToShow":
            result = GLib.Variant("(b)", (menu.rebuild() if args[0] == 0 else False,))
        elif method == "AboutToShowGroup":
            changed = menu.rebuild() if 0 in args[0] else False
            result = GLib.Variant("(aiai)", ([0] if changed else [], []))
        else:
            invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
            return
        invocation.return_value(result)

    def _activate_later(self, number: int) -> None:
        """Après avoir répondu à la barre : l'action peut ouvrir la carte, changer de fenêtre…"""

        def run() -> bool:
            self.menu.activate(number)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(run)

    def _on_menu_property(self, _bus, _sender, _path, _interface, name: str) -> GLib.Variant | None:
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }.get(name)
