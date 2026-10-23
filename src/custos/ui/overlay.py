"""Fenêtre de la mascotte : surface gtk4-layer-shell transparente, au-dessus de tout.

Toutes les méthodes de ce module s'exécutent dans le thread GTK. Les autres
threads (serveur IPC, appels à Claude) passent par OverlayUI, qui s'y ramène
avec GLib.idle_add.
"""

import logging
import math
import threading
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Graphene, Gsk, Gtk, Pango
from gi.repository import Gtk4LayerShell as LayerShell

from custos.config import MascotConfig
from custos.context import hyprland
from custos.states import MascotState
from custos.ui import position
from custos.ui.animation import ALL_SPRITES, Animator
from custos.ui.bubble import CSS as BUBBLE_CSS
from custos.ui.bubble import WIDTH as BUBBLE_WIDTH
from custos.ui.bubble import Bubble
from custos.ui.position import PADDING
from custos.ui.tray import Tray

log = logging.getLogger(__name__)

APP_ID = "io.github.touya0.Custos"
HALO_COLOR = (245, 245, 250)
HALO_OPACITY = 0.9

BADGE_RADIUS = 0.13  # fraction de la taille de la mascotte
BADGE_PULSE = 1.4  # secondes
BADGE_BORDER_WIDTH = 2.0
BADGE_FILL = Gdk.RGBA(red=0.937, green=0.914, blue=0.875, alpha=1.0)  # crème #efe9df
BADGE_BORDER = Gdk.RGBA(red=0.122, green=0.176, blue=0.310, alpha=1.0)  # marine #1f2d4f
BADGE_ICONS = {
    "selection": "📋",
    "clipboard-image": "🖼\ufe0f",
    "screenshot": "🖼\ufe0f",
    "agent-waiting": "✋",  # une session Claude Code attend ta permission
    "agent-done": "✅",  # une session Claude Code a fini
}
PERSISTENT_BADGES = {"agent-waiting", "agent-done"}  # restent jusqu'à ce que tu ailles voir

FOLLOW_INTERVAL = 200  # ms entre deux lectures de la position de la fenêtre accrochée
DRAG_THRESHOLD = 6  # pixels : en dessous, c'est un clic
FALLBACK_MONITOR = hyprland.Monitor(0, 0, 1920, 1080)  # si Hyprland ne répond pas
MONITOR_RETRIES = 10  # écran tout juste branché : GTK le découvre parfois après Hyprland
MONITOR_RETRY_INTERVAL = 300  # ms

DEFAULT_SPRITES = Path(__file__).resolve().parents[3] / "assets" / "sprites"
DEFAULT_ICON = DEFAULT_SPRITES.parent / "icon.png"

def _gdk_monitor(name: str) -> Gdk.Monitor | None:
    """Écran GTK correspondant au connecteur Hyprland (« eDP-1 »…)."""
    monitors = Gdk.Display.get_default().get_monitors()
    for i in range(monitors.get_n_items()):
        monitor = monitors.get_item(i)
        if monitor.get_connector() == name:
            return monitor
    return None


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
    for name in ALL_SPRITES:
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
        self.notice: str | None = None  # badge affiché (voir watch.SELECTION…)
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
        if self.notice:
            self._draw_badge(snapshot)
        snapshot.restore()

    def _draw_badge(self, snapshot: Gtk.Snapshot) -> None:
        """Pastille en haut à droite : Custos a remarqué quelque chose (📋 texte, 🖼 image)."""
        pulse = 1 + 0.08 * math.sin(2 * math.pi * time.monotonic() / BADGE_PULSE)
        radius = self.size * BADGE_RADIUS * pulse
        cx, cy = self.size * 0.34, -self.size * 0.36
        bounds = Graphene.Rect()
        bounds.init(cx - radius, cy - radius, 2 * radius, 2 * radius)
        circle = Gsk.RoundedRect()
        circle.init_from_rect(bounds, radius)

        snapshot.push_rounded_clip(circle)
        snapshot.append_color(BADGE_FILL, bounds)
        snapshot.pop()
        snapshot.append_border(circle, [BADGE_BORDER_WIDTH] * 4, [BADGE_BORDER] * 4)

        layout = self.create_pango_layout(BADGE_ICONS.get(self.notice, "✨"))
        font = Pango.FontDescription.from_string("sans")
        font.set_absolute_size(radius * 1.1 * Pango.SCALE)
        layout.set_font_description(font)
        _ink, logical = layout.get_pixel_extents()
        origin = Graphene.Point()
        origin.init(cx - logical.width / 2, cy - logical.height / 2)
        snapshot.save()
        snapshot.translate(origin)
        snapshot.append_layout(layout, BADGE_BORDER)
        snapshot.restore()


def _short_name(window: hyprland.ActiveWindow, limit: int = 32) -> str:
    """Nom lisible d'une fenêtre pour le menu : son titre, sinon sa classe."""
    name = window.title or window.window_class
    return name if len(name) <= limit else name[: limit - 1] + "…"


class MascotOverlay:
    def __init__(self, app: Gtk.Application, config: MascotConfig) -> None:
        folder = Path(config.sprites_dir).expanduser() if config.sprites_dir else DEFAULT_SPRITES
        self.app = app
        self.config = config
        self.animator = Animator(config.sleep_after, now=time.monotonic())
        self._notice_timer = 0
        self.attached: str | None = None  # adresse Hyprland de la fenêtre suivie
        self._attach_offset = (0, 0)
        self._follow_timer = 0
        self._hidden_fullscreen = False
        self._hidden_offscreen = False  # fenêtre accrochée sur un autre espace de travail
        self.can_stow = config.tray  # icône dans la barre pour la retrouver
        self._stowed = config.tray and position.load_stowed()

        self.window = Gtk.Window(application=app, title="Custos", decorated=False)
        self.window.add_css_class("custos")
        LayerShell.init_for_window(self.window)
        LayerShell.set_namespace(self.window, "custos")
        LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
        LayerShell.set_keyboard_mode(self.window, LayerShell.KeyboardMode.NONE)
        # Toujours ancrée en haut à gauche : les marges sont sa position à l'écran.
        # Zone exclusive -1 : marges comptées depuis le bord de l'écran, pas depuis la
        # zone laissée libre par la barre caelestia.
        LayerShell.set_exclusive_zone(self.window, -1)
        LayerShell.set_anchor(self.window, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self.window, LayerShell.Edge.LEFT, True)

        scale = self.window.get_scale_factor()
        sprites = load_sprites(folder, config.size * scale, config.outline)
        self.widget = MascotWidget(config, sprites, self.animator)
        self.bubble = Bubble(
            app,
            on_submit=lambda question: app.on_submit(question),
            on_close=lambda: app.on_close(),
            on_open_full=lambda: app.on_open_full(),
            on_new=lambda: app.on_new(),
        )

        self.canvas = round(config.size * (1 + 2 * PADDING))
        # Écran où elle vit : ses coordonnées (self.x, self.y) sont relatives à son coin.
        self.monitor = FALLBACK_MONITOR
        self._bound_monitor: str | None = None  # écran GTK auquel les surfaces sont liées
        self._monitor_retry = 0
        self._monitor_retries = 0
        self._use_monitor(self._start_monitor())
        self.move_to(*self._free_position())

        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.widget.add_controller(click)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.widget.add_controller(drag)
        self._drag_start: tuple[tuple[int, int], tuple[int, int]] | None = None
        self._dragged = False
        self._drag_windows: list[hyprland.ActiveWindow] = []
        self._drag_monitors: list[hyprland.Monitor] = []
        self._build_menu()
        self.window.set_child(self.widget)

        css = Gtk.CssProvider()
        css.load_from_string("window.custos { background: transparent; }" + BUBBLE_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    @property
    def state(self) -> MascotState:
        return self.animator.state

    @property
    def screen(self) -> tuple[int, int]:
        return self.monitor.width, self.monitor.height

    @property
    def origin(self) -> tuple[int, int]:
        return self.monitor.x, self.monitor.y

    def set_state(self, state: MascotState) -> None:
        self.animator.set_state(state, time.monotonic())
        if self.attached:
            self._follow()  # assise ↔ debout : elle se replace aussitôt sur le bord

    def move_to(self, x: int, y: int) -> None:
        self.x, self.y = position.clamp(x, y, self.canvas, *self.screen)
        LayerShell.set_margin(self.window, LayerShell.Edge.LEFT, self.x)
        LayerShell.set_margin(self.window, LayerShell.Edge.TOP, self.y)
        self.bubble.place(
            position.bubble_placement(self.x, self.y, self.canvas, BUBBLE_WIDTH, *self.screen)
        )

    # Glisser-déposer : on suit le curseur global (socket Hyprland) plutôt que les
    # coordonnées du geste, qui bougent avec la fenêtre qu'on est en train de déplacer.

    def _on_drag_begin(self, *_args) -> None:
        cursor = hyprland.cursor_position()
        # Départ en coordonnées globales : elle peut passer d'un écran à l'autre en chemin.
        self._drag_start = (cursor, self._global_position()) if cursor else None
        self._dragged = False
        # Lus une fois : les fenêtres et les écrans ne bougent pas pendant que tu la déplaces.
        self._drag_windows = hyprland.visible_windows()
        self._drag_monitors = hyprland.monitors()

    def _on_drag_update(self, *_args) -> None:
        cursor = hyprland.cursor_position()
        if not self._drag_start or not cursor:
            return
        (cx, cy), (x, y) = self._drag_start
        dx, dy = cursor[0] - cx, cursor[1] - cy
        if not self._dragged and abs(dx) + abs(dy) < DRAG_THRESHOLD:
            return  # simple clic, pas un déplacement
        self._dragged = True
        # Elle passe sur l'écran où se trouve le curseur.
        monitor = next((m for m in self._drag_monitors if m.contains(*cursor)), None)
        if monitor and monitor.name != self.monitor.name:
            self._use_monitor(monitor)
        self.move_to(x + dx - self.origin[0], y + dy - self.origin[1])
        # Au-dessus du bord haut d'une fenêtre, elle prend la pose assise : la lâcher là l'y accroche.
        self.animator.sitting = self._drop_target() is not None

    def _drop_target(self) -> hyprland.ActiveWindow | None:
        # Rectangles recalculés : l'écran de la mascotte a pu changer pendant le déplacement.
        windows = [(window, self._rect(window)) for window in self._drag_windows]
        return position.edge_target(self.x, self.y, self.config.size, self.canvas, windows)

    def _global_position(self) -> tuple[int, int]:
        return self.origin[0] + self.x, self.origin[1] + self.y

    def _on_drag_end(self, *_args) -> None:
        moved = self._drag_start and self._global_position() != self._drag_start[1]
        self._drag_start = None
        if not moved:
            self.animator.sitting = self.attached is not None
            return
        target = self._drop_target()
        if target:
            # Lâchée sur le bord haut d'une fenêtre (la sienne ou une autre) : elle s'y assoit.
            self.attach(target, offset=position.edge_offset(self.x, self.canvas, self._rect(target)))
            return
        self.detach(move_back=False)  # lâchée ailleurs : elle est libre, là où tu l'as posée
        self.animator.sitting = False
        position.save(self.x, self.y, self.monitor.name)

    def _on_click(self, *_args) -> None:
        if self._dragged:
            return  # fin d'un déplacement, pas un clic
        # Un clic ouvre la bulle (ou la ferme si elle est déjà ouverte).
        if self.bubble.visible:
            self.bubble.close()
        else:
            self.app.on_open()
        if self.state is not MascotState.THINKING:
            self.set_state(MascotState.IDLE)

    def set_hidden(self, hidden: bool) -> None:
        self._hidden_fullscreen = hidden
        self._update_visibility()

    def _update_visibility(self) -> None:
        # Seule la mascotte disparaît : une bulle ouverte reste là où tu l'as laissée.
        hidden = self._hidden_fullscreen or self._hidden_offscreen or self._stowed
        self.window.set_visible(not hidden)

    def toggle_stowed(self) -> None:
        """Range la mascotte dans la barre, ou l'en fait ressortir."""
        self._stowed = not self._stowed
        position.save_stowed(self._stowed)
        self._update_visibility()

    # --- accrochage à une fenêtre ----------------------------------------------

    def _rect(self, window: hyprland.ActiveWindow) -> position.Rect:
        """Fenêtre en coordonnées de l'écran de la mascotte."""
        return position.Rect(
            window.x - self.origin[0], window.y - self.origin[1], window.width, window.height
        )

    def _free_position(self) -> tuple[int, int]:
        """Là où tu l'as posée sur cet écran, sinon le coin choisi dans la config."""
        saved = position.load()
        if saved and position.load_monitor() in (None, self.monitor.name):
            return saved
        return position.default_position(
            self.config.position, self.config.margin, self.canvas, *self.screen
        )

    # --- écrans ------------------------------------------------------------------

    def _start_monitor(self) -> hyprland.Monitor:
        """Écran où tu l'as posée la dernière fois s'il est branché, sinon l'écran actif."""
        found = hyprland.monitors()
        saved = position.load_monitor()
        return (
            next((m for m in found if m.name == saved), None)
            or next((m for m in found if m.focused), None)
            or (found[0] if found else FALLBACK_MONITOR)
        )

    def _use_monitor(self, monitor: hyprland.Monitor) -> None:
        """Installe la mascotte (et la bulle) sur cet écran."""
        changed = monitor.name != self.monitor.name
        self.monitor = monitor
        if monitor.name and monitor.name != self._bound_monitor:
            gdk_monitor = _gdk_monitor(monitor.name)
            if gdk_monitor is None:
                self._retry_monitor_later()
            else:
                LayerShell.set_monitor(self.window, gdk_monitor)
                self.bubble.set_monitor(gdk_monitor)
                self._bound_monitor = monitor.name
                self._monitor_retries = 0
        if changed:
            log.info("mascotte sur l'écran %s", monitor.name)
            self.app.on_monitor(monitor.name)

    def _retry_monitor_later(self) -> None:
        if self._monitor_retry or self._monitor_retries >= MONITOR_RETRIES:
            return
        self._monitor_retries += 1

        def retry() -> bool:
            self._monitor_retry = 0
            self._use_monitor(self.monitor)
            return GLib.SOURCE_REMOVE

        self._monitor_retry = GLib.timeout_add(MONITOR_RETRY_INTERVAL, retry)

    def monitors_changed(self) -> None:
        """Écran branché ou débranché."""
        found = hyprland.monitors()
        if not found:
            return
        current = next((m for m in found if m.name == self.monitor.name), None)
        saved = position.load_monitor()
        home = next((m for m in found if m.name == saved), None)
        if home and home != current and not self.attached:
            # Son écran est rebranché : elle y retourne, là où tu l'avais posée.
            self._use_monitor(home)
            self.move_to(*self._free_position())
        elif current is None:
            # Son écran a disparu : elle passe sur l'écran actif (sans oublier l'autre).
            self._use_monitor(next((m for m in found if m.focused), found[0]))
            self.move_to(*self._free_position())
        else:
            self._use_monitor(current)  # même écran, peut-être une autre résolution
            self.move_to(self.x, self.y)
        if self.attached:
            self._follow()

    def _follow_monitor(self, window: hyprland.ActiveWindow) -> None:
        """Fenêtre accrochée passée sur un autre écran : la mascotte la suit."""
        # Là où elle s'assoit : un peu sous le bord haut, qui peut dépasser de l'écran.
        x = window.x + max(0, min(self._attach_offset, window.width - self.canvas)) + self.canvas / 2
        y = window.y + min(window.height / 2, self.canvas)
        if self.monitor.contains(x, y):
            return
        found = hyprland.monitors()
        # Sinon l'écran où Hyprland range la fenêtre (il tarde parfois pour une fenêtre flottante).
        monitor = next((m for m in found if m.contains(x, y)), None) or next(
            (m for m in found if m.id == window.monitor), None
        )
        if monitor and monitor.name != self.monitor.name:
            self._use_monitor(monitor)

    def attach(self, window: hyprland.ActiveWindow, offset: int | None = None) -> None:
        """S'assoit sur le bord haut de la fenêtre, à `offset` pixels de son bord gauche.

        Sans `offset` (menu, `custos attach`) : au-dessus de l'endroit où elle est si
        elle est déjà sur la fenêtre, sinon près du coin haut-gauche.
        """
        rect = self._rect(window)
        self.attached = window.address
        self.animator.sitting = True
        if offset is None:
            offset = (
                position.edge_offset(self.x, self.canvas, rect)
                if position.inside(self.x, self.y, self.canvas, rect)
                else position.ATTACH_MARGIN
            )
        self._attach_offset = offset
        if not self._follow_timer:
            self._follow_timer = GLib.timeout_add(FOLLOW_INTERVAL, self._follow)
        self._follow()
        self.app.on_attached(window)

    def detach(self, move_back: bool = True) -> None:
        """Libère la mascotte ; `move_back` la ramène à sa dernière position libre."""
        if not self.attached:
            return
        self.attached = None
        self.animator.sitting = False
        if self._follow_timer:
            GLib.source_remove(self._follow_timer)
            self._follow_timer = 0
        self._hidden_offscreen = False
        self._update_visibility()
        if move_back:
            self.move_to(*self._free_position())
        self.app.on_attached(None)

    def _follow(self) -> bool:
        """Suit la fenêtre accrochée (déplacement, taille, espace de travail, fermeture)."""
        if not self.attached:
            return GLib.SOURCE_REMOVE
        if self._drag_start:
            return GLib.SOURCE_CONTINUE  # tu la déplaces : on ne la ramène pas
        window = hyprland.find_window(self.attached)
        if window is None:
            self._follow_timer = 0  # ce minuteur s'arrête en renvoyant SOURCE_REMOVE
            self.detach()
            return GLib.SOURCE_REMOVE
        offscreen = window.workspace not in hyprland.visible_workspaces()
        if offscreen != self._hidden_offscreen:
            self._hidden_offscreen = offscreen
            self._update_visibility()
        if not offscreen:
            self._follow_monitor(window)
            x, y = position.on_edge(
                self._attach_offset, self.config.size, self.canvas, self._rect(window), self.animator.is_sitting
            )
            if (x, y) != (self.x, self.y):
                self.move_to(x, y)
        return GLib.SOURCE_CONTINUE

    # --- menu du clic droit ------------------------------------------------------

    def _build_menu(self) -> None:
        actions = Gio.SimpleActionGroup()
        for name, callback in (
            ("attach", self._attach_candidate),
            ("detach", self.detach),
            ("reset", self._reset_position),
            ("stow", self.toggle_stowed),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_args, callback=callback: callback())
            actions.add_action(action)
        self.widget.insert_action_group("mascot", actions)

        self.menu = Gtk.PopoverMenu(has_arrow=False)
        self.menu.set_parent(self.widget)
        right_click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        right_click.connect("pressed", self._on_right_click)
        self.widget.add_controller(right_click)
        self._candidate: hyprland.ActiveWindow | None = None

    def _on_right_click(self, _gesture, _n_press: int, x: float, y: float) -> None:
        model = Gio.Menu()
        if self.attached:
            window = hyprland.find_window(self.attached)
            name = _short_name(window) if window else "la fenêtre"
            model.append(f"Se détacher de « {name} »", "mascot.detach")
        else:
            # La mascotte ne prend jamais le focus : la fenêtre active est celle où tu étais.
            self._candidate = hyprland.active_window()
            if self._candidate and self._candidate.address:
                model.append(f"S'accrocher à « {_short_name(self._candidate)} »", "mascot.attach")
        model.append("Remettre dans le coin", "mascot.reset")
        if self.can_stow:
            model.append("Se ranger dans la barre", "mascot.stow")
        self.menu.set_menu_model(model)
        target = Gdk.Rectangle()
        target.x, target.y, target.width, target.height = int(x), int(y), 1, 1
        self.menu.set_pointing_to(target)
        self.menu.popup()

    def _attach_candidate(self) -> None:
        if self._candidate:
            self.attach(self._candidate)

    def _reset_position(self) -> None:
        position.forget()
        self.detach(move_back=False)
        self.move_to(*self._free_position())

    def show_notice(self, kind: str | None, duration: float) -> None:
        self.widget.notice = kind
        if self._notice_timer:
            GLib.source_remove(self._notice_timer)
            self._notice_timer = 0
        if kind:
            if self.state is MascotState.SLEEPING:
                self.set_state(MascotState.IDLE)  # tu es là : elle se réveille
            if kind not in PERSISTENT_BADGES:
                self._notice_timer = GLib.timeout_add(int(duration * 1000), self._expire_notice)

    def _expire_notice(self) -> bool:
        self._notice_timer = 0
        self.widget.notice = None
        return GLib.SOURCE_REMOVE

    def show(self) -> None:
        self.window.present()
        self._update_visibility()  # peut-être rangée depuis la dernière session


class OverlayUI:
    """Interface du daemon (voir daemon.UI), utilisable depuis n'importe quel thread."""

    def __init__(self, app: "MascotApp", notice_duration: float) -> None:
        self.app = app
        self.notice_duration = notice_duration

    def connect(self, daemon) -> None:
        """Relie la mascotte et la bulle au daemon."""
        self.app.on_submit = daemon.submit
        self.app.on_close = daemon.close_bubble
        # Le clic arrive dans le thread GTK ; lire le contexte appelle hyprctl et wl-paste.
        self.app.on_open = lambda: threading.Thread(
            target=daemon.open_from_mascot, daemon=True
        ).start()
        self.app.on_attached = daemon.set_attached
        self.app.on_monitor = daemon.set_monitor
        self.app.on_open_full = daemon.open_full
        # Lire le contexte appelle hyprctl et wl-paste : hors du thread GTK.
        self.app.on_new = lambda: threading.Thread(target=daemon.new_conversation, daemon=True).start()

    def _on_gtk_thread(self, action: Callable[[MascotOverlay], None]) -> None:
        def run() -> bool:
            if self.app.overlay:
                action(self.app.overlay)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(run)

    def set_state(self, state: MascotState) -> None:
        self._on_gtk_thread(lambda overlay: overlay.set_state(state))

    def open_prompt(self, summary: str, hint: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.open_prompt(summary, hint))

    def start_answer(self, question: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.start_answer(question))

    def append_answer(self, text: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.append_answer(text))

    def end_answer(self, answer: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.end_answer(answer))

    def reopen(self, summary: str, question: str, answer: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.reopen(summary, question, answer))

    def show_error(self, text: str) -> None:
        self._on_gtk_thread(lambda overlay: overlay.bubble.show_error(text))

    def show_notice(self, kind: str | None) -> None:
        self._on_gtk_thread(lambda overlay: overlay.show_notice(kind, self.notice_duration))

    def set_hidden(self, hidden: bool) -> None:
        self._on_gtk_thread(lambda overlay: overlay.set_hidden(hidden))

    def attach(self, window) -> None:
        self._on_gtk_thread(lambda overlay: overlay.attach(window))

    def detach(self) -> None:
        self._on_gtk_thread(lambda overlay: overlay.detach())

    def toggle_stowed(self) -> None:
        self._on_gtk_thread(lambda overlay: overlay.toggle_stowed())

    def monitors_changed(self) -> None:
        self._on_gtk_thread(lambda overlay: overlay.monitors_changed())


class MascotApp(Gtk.Application):
    def __init__(self, config: MascotConfig) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.config = config
        self.overlay: MascotOverlay | None = None
        self.tray: Tray | None = None
        # Remplacés par OverlayUI.connect une fois le daemon créé.
        self.on_submit: Callable[[str], None] = lambda question: None
        self.on_close: Callable[[], None] = lambda: None
        self.on_open: Callable[[], None] = lambda: None
        self.on_open_full: Callable[[], None] = lambda: None
        self.on_new: Callable[[], None] = lambda: None
        self.on_attached: Callable[[hyprland.ActiveWindow | None], None] = lambda window: None
        self.on_monitor: Callable[[str], None] = lambda name: None
        self.connect("activate", self._on_activate)

    def _on_activate(self, app: Gtk.Application) -> None:
        if self.overlay is None:
            self.overlay = MascotOverlay(self, self.config)
            if self.config.tray:
                self.tray = Tray(
                    DEFAULT_ICON,
                    on_activate=self.overlay.toggle_stowed,
                    on_secondary=lambda: self.on_open(),
                )
                self.tray.start()
        self.overlay.show()
