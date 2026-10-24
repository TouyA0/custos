"""Icônes de Custos : un seul jeu, dessiné ici (pas d'emoji, pas de thème d'icônes).

Tracés SVG sur une grille de 24, au trait arrondi : nets à toute taille, et de la couleur
qu'on leur donne (celle du texte de la carte, d'un badge…). Les mêmes tracés servent dans
GTK (Gsk.Path, voir `draw` et `Icon`) et en image PNG pour le menu de la barre (`png`).
"""

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GdkPixbuf, Graphene, Gsk, Gtk

GRID = 24
STROKE = 2.0

# Chaque icône : des tracés (données SVG « d ») au trait, et éventuellement pleins.
ICONS: dict[str, tuple[list[str], list[str]]] = {
    "plus": (["M12 5v14", "M5 12h14"], []),
    "close": (["M6 6l12 12", "M18 6L6 18"], []),
    "open": (["M14 4h6v6", "M20 4l-9 9", "M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4"], []),
    "stop": ([], ["M8.5 7h7a1.5 1.5 0 0 1 1.5 1.5v7a1.5 1.5 0 0 1-1.5 1.5h-7A1.5 1.5 0 0 1 7 15.5v-7A1.5 1.5 0 0 1 8.5 7z"]),
    "copy": ([
        "M10 8h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z",
        "M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2",
    ], []),
    "check": (["M20 6L9 17l-5-5"], []),
    "alert": (["M10.3 4.9a2 2 0 0 1 3.4 0l7.1 12.2A2 2 0 0 1 19.1 20H4.9a2 2 0 0 1-1.7-2.9z", "M12 9.5v4"],
              ["M12 15.9a1.1 1.1 0 1 1 0 2.2a1.1 1.1 0 1 1 0-2.2z"]),
    "image": ([
        "M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z",
        "M20 15l-4-4-9 9",
        "M9 7.5a1.5 1.5 0 1 1 0 3a1.5 1.5 0 1 1 0-3z",
    ], []),
    "screenshot": ([
        "M4 8V6a2 2 0 0 1 2-2h2", "M16 4h2a2 2 0 0 1 2 2v2",
        "M20 16v2a2 2 0 0 1-2 2h-2", "M8 20H6a2 2 0 0 1-2-2v-2",
        "M9 12h6", "M12 9v6",
    ], []),
    "text": (["M4 7h9", "M4 12h9", "M4 17h6", "M18 5v14", "M16 5h4", "M16 19h4"], []),
    "attention": (["M12 5.5v8"], ["M12 16.4a1.4 1.4 0 1 1 0 2.8a1.4 1.4 0 1 1 0-2.8z"]),
    "working": (["M12 4a8 8 0 1 1-8 8"], []),
    "sparkle": ([], ["M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"]),
    "chat": (["M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-8l-4 4v-4H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"], []),
    "history": (["M3.5 12a8.5 8.5 0 1 0 2.5-6", "M3.5 4v4h4", "M12 8v4l3 2"], []),
    "quit": (["M12 3v8", "M17.7 6.8a8 8 0 1 1-11.4 0"], []),
    "bell": (["M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 1.5h-15z", "M10 20.5a2 2 0 0 0 4 0"], []),
}

_paths: dict[str, Gsk.Path] = {}


def _path(data: str) -> Gsk.Path:
    path = _paths.get(data)
    if path is None:
        path = _paths[data] = Gsk.Path.parse(data)
    return path


def draw(snapshot: Gtk.Snapshot, name: str, size: float, color: Gdk.RGBA) -> None:
    """Dessine l'icône dans un carré de `size` pixels, à l'origine du snapshot."""
    strokes, fills = ICONS.get(name, ICONS["sparkle"])
    stroke = Gsk.Stroke.new(STROKE)
    stroke.set_line_cap(Gsk.LineCap.ROUND)
    stroke.set_line_join(Gsk.LineJoin.ROUND)
    snapshot.save()
    snapshot.scale(size / GRID, size / GRID)
    for data in strokes:
        snapshot.append_stroke(_path(data), stroke, color)
    for data in fills:
        snapshot.append_fill(_path(data), Gsk.FillRule.WINDING, color)
    snapshot.restore()


class Icon(Gtk.Widget):
    """Icône de la couleur du texte de son widget (CSS `color`) : elle suit le survol des
    boutons et le thème."""

    def __init__(self, name: str, size: int = 16) -> None:
        super().__init__()
        self.name = name
        self.size = size
        self.set_size_request(size, size)
        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.set_can_target(False)  # le clic va au bouton
        # Survol ou appui du bouton : la couleur change, on redessine.
        self.connect("state-flags-changed", lambda *_args: self.queue_draw())

    def set_icon(self, name: str) -> None:
        self.name = name
        self.queue_draw()

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        origin = Graphene.Point()
        origin.init((self.get_width() - self.size) / 2, (self.get_height() - self.size) / 2)
        snapshot.save()
        snapshot.translate(origin)
        draw(snapshot, self.name, self.size, self.get_color())
        snapshot.restore()


def svg(name: str, color: str, size: int) -> str:
    """L'icône en SVG, d'une couleur (« #4caf50 ») et d'une taille données."""
    strokes, fills = ICONS.get(name, ICONS["sparkle"])
    paths = [
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{STROKE}" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
        for d in strokes
    ] + [f'<path d="{d}" fill="{color}"/>' for d in fills]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" '
        f'width="{size}" height="{size}">{"".join(paths)}</svg>'
    )


def pixbuf(name: str, color: str, size: int) -> GdkPixbuf.Pixbuf:
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.write(svg(name, color, size).encode())
    loader.close()
    return loader.get_pixbuf()


def png(name: str, color: str, size: int = 32) -> bytes:
    """L'icône en PNG : pour le menu de la barre, qui reçoit des images toutes faites."""
    return pixbuf(name, color, size).save_to_bufferv("png", [], [])[1]


def texture(name: str, color: str, size: int) -> Gdk.Texture:
    """L'icône en texture, là où GTK veut une image (icône du champ de saisie)."""
    return Gdk.Texture.new_for_pixbuf(pixbuf(name, color, size))
