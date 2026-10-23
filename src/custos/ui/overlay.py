"""Fenêtre de la mascotte : surface gtk4-layer-shell transparente, au-dessus de tout.

Toutes les méthodes de ce module s'exécutent dans le thread GTK. Les autres
threads (serveur IPC, appels à Claude) passent par OverlayUI, qui s'y ramène
avec GLib.idle_add.
"""

import threading
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Graphene, Gtk
from gi.repository import Gtk4LayerShell as LayerShell

from custos.config import MascotConfig
from custos.states import MascotState
from custos.ui.animation import BLINK_SPRITE, SPRITES, Animator
from custos.ui.bubble import CSS as BUBBLE_CSS
from custos.ui.bubble import Bubble

APP_ID = "io.github.touya0.Custos"
PADDING = 0.1  # marge autour du sprite (fraction de la taille) pour le flottement et le rebond
HALO_COLOR = (245, 245, 250)
HALO_OPACITY = 0.9

DEFAULT_SPRITES = Path(__file__).resolve().parents[3] / "assets" / "sprites"

EDGES = {
    "top": LayerShell.Edge.TOP,
    "bottom": LayerShell.Edge.BOTTOM,
    "left": LayerShell.Edge.LEFT,
    "right": LayerShell.Edge.RIGHT,
}


def is_supported() -> bool:
    return LayerShell.is_supported()


def _texture(width: int, height: int, rgba: bytes, rowstride: int) -> Gdk.Texture:
    return Gdk.MemoryTexture.new(
        width, height, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(rgba), rowstride
    )


def _halo(pixbuf: GdkPixbuf.Pixbuf, radius: int) -> Gdk.Texture:
    """Silhouette claire légèrement élargie, dessinée derrière le sprite."""
    width, height, stride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
    pixels = pixbuf.get_pixels()
    alpha = [pixels[y * stride + 3 : y * stride + width * 4 : 4] for y in range(height)]
    offsets = [
        (dx, dy)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
        if dx * dx + dy * dy <= radius * radius
    ]

    r, g, b = HALO_COLOR
    out = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            a = 0
            for dx, dy in offsets:
                sx, sy = x + dx, y + dy
                if 0 <= sx < width and 0 <= sy < height and alpha[sy][sx] > a:
                    a = alpha[sy][sx]
            if a:
                i = (y * width + x) * 4
                out[i : i + 4] = bytes((r, g, b, int(a * HALO_OPACITY)))
    return _texture(width, height, bytes(out), width * 4)


def load_sprites(folder: Path, pixels: int, outline: bool) -> dict[str, tuple[Gdk.Texture, Gdk.Texture | None]]:
    """Charge chaque sprite à la taille d'affichage, avec son halo éventuel."""
    sprites = {}
    for name in [*SPRITES.values(), BLINK_SPRITE]:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(folder / f"{name}.png"), pixels, pixels, True)
        if not pixbuf.get_has_alpha():
            pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
        texture = _texture(pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_pixels(), pixbuf.get_rowstride())
        halo = _halo(pixbuf, max(1, round(pixels * 0.02))) if outline else None
        sprites[name] = (texture, halo)
    return sprites


class MascotWidget(Gtk.Widget):
    def __init__(self, config: MascotConfig, sprites: dict, animator: Animator) -> None:
        super().__init__()
        self.size = config.size
        self.sprites = sprites
        self.animator = animator
        canvas = round(self.size * (1 + 2 * PADDING))
        self.set_size_request(canvas, canvas)
        self._fps = 0
        self._tick()

    def _tick(self) -> bool:
        self.queue_draw()
        # La cadence suit l'état : on reprogramme le minuteur quand elle change.
        fps = self.animator.fps(time.monotonic())
        if fps == self._fps:
            return GLib.SOURCE_CONTINUE
        self._fps = fps
        GLib.timeout_add(1000 // fps, self._tick)
        return GLib.SOURCE_REMOVE

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        frame = self.animator.frame(time.monotonic())
        texture, halo = self.sprites[frame.sprite]
        center = Graphene.Point()
        center.init(
            self.get_width() / 2 + frame.dx * self.size,
            self.get_height() / 2 + frame.dy * self.size,
        )
        rect = Graphene.Rect()
        rect.init(-self.size / 2, -self.size / 2, self.size, self.size)

        snapshot.save()
        snapshot.translate(center)
        snapshot.scale(frame.scale, frame.scale)
        if halo:
            snapshot.append_texture(halo, rect)
        snapshot.append_texture(texture, rect)
        snapshot.restore()


class MascotOverlay:
    def __init__(self, app: Gtk.Application, config: MascotConfig) -> None:
        folder = Path(config.sprites_dir).expanduser() if config.sprites_dir else DEFAULT_SPRITES
        self.app = app
        self.animator = Animator(config.sleep_after, now=time.monotonic())

        self.window = Gtk.Window(application=app, title="Custos", decorated=False)
        self.window.add_css_class("custos")
        LayerShell.init_for_window(self.window)
        LayerShell.set_namespace(self.window, "custos")
        LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(self.window, LayerShell.KeyboardMode.NONE)
        for edge in config.position.split("-"):
            LayerShell.set_anchor(self.window, EDGES[edge], True)
            LayerShell.set_margin(self.window, EDGES[edge], config.margin)

        scale = self.window.get_scale_factor()
        sprites = load_sprites(folder, config.size * scale, config.outline)
        self.widget = MascotWidget(config, sprites, self.animator)
        self.bubble = Bubble(
            app,
            config,
            mascot_height=round(config.size * (1 + 2 * PADDING)),
            on_submit=lambda question: app.on_submit(question),
            on_close=lambda: app.on_close(),
        )
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.widget.add_controller(click)
        self.window.set_child(self.widget)

        css = Gtk.CssProvider()
        css.load_from_string("window.custos { background: transparent; }" + BUBBLE_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    @property
    def state(self) -> MascotState:
        return self.animator.state

    def set_state(self, state: MascotState) -> None:
        self.animator.set_state(state, time.monotonic())

    def _on_click(self, *_args) -> None:
        # Un clic ouvre la bulle (ou la ferme si elle est déjà ouverte).
        if self.bubble.visible:
            self.bubble.close()
        else:
            self.app.on_open()
        if self.state is not MascotState.THINKING:
            self.set_state(MascotState.IDLE)

    def show(self) -> None:
        self.window.present()


class OverlayUI:
    """Interface du daemon (voir daemon.UI), utilisable depuis n'importe quel thread."""

    def __init__(self, app: "MascotApp") -> None:
        self.app = app

    def connect(self, daemon) -> None:
        """Relie la mascotte et la bulle au daemon."""
        self.app.on_submit = daemon.submit
        self.app.on_close = daemon.close_session
        # Le clic arrive dans le thread GTK ; lire le contexte appelle hyprctl et wl-paste.
        self.app.on_open = lambda: threading.Thread(
            target=daemon.ask, args=({"type": "ask"},), daemon=True
        ).start()

    def _on_gtk_thread(self, action: Callable[[MascotOverlay], None]) -> None:
        def run() -> bool:
            if self.app.overlay:
                action(self.app.overlay)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(run)

    def set_state(self, state: MascotState) -> None:
        self._on_gtk_thread(lambda overlay: overlay.set_state(state))

    def open_prompt(self, summary: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.open_prompt(summary))

    def start_answer(self, question: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.start_answer(question))

    def append_answer(self, text: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.append_answer(text))

    def end_answer(self, answer: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.end_answer(answer))

    def show_error(self, text: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.show_error(text))


class MascotApp(Gtk.Application):
    def __init__(self, config: MascotConfig) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.config = config
        self.overlay: MascotOverlay | None = None
        # Remplacés par OverlayUI.connect une fois le daemon créé.
        self.on_submit: Callable[[str], None] = lambda question: None
        self.on_close: Callable[[], None] = lambda: None
        self.on_open: Callable[[], None] = lambda: None
        self.connect("activate", self._on_activate)

    def _on_activate(self, app: Gtk.Application) -> None:
        if self.overlay is None:
            self.overlay = MascotOverlay(self, self.config)
        self.overlay.show()
