"""Bulle de dialogue : champ de saisie et réponse affichée au fil de l'eau.

Fenêtre layer-shell à part, placée juste au-dessus (ou en dessous) de la mascotte.
Toutes les méthodes s'exécutent dans le thread GTK.
"""

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango
from gi.repository import Gtk4LayerShell as LayerShell

from custos.config import MascotConfig
from custos.context import clipboard
from custos.ui import markdown

WIDTH = 440
MAX_HEIGHT = 380
QUESTION_COLOR = "#f6c453"

CSS = """
window.custos-bubble { background: transparent; }
.bubble {
    background-color: rgba(24, 31, 52, 0.97);
    color: #efe9df;
    border-radius: 16px;
    border: 1px solid rgba(239, 233, 223, 0.15);
    padding: 10px 14px 12px 14px;
}
.bubble label { color: #efe9df; }
.bubble .title { font-weight: bold; color: #f6c453; }
.bubble .summary { font-size: 0.85em; color: rgba(239, 233, 223, 0.6); }
.bubble .error { color: #ff9b8a; }
.bubble scrolledwindow, .bubble viewport { background: transparent; }
.bubble entry {
    background-color: rgba(239, 233, 223, 0.08);
    color: #efe9df;
    caret-color: #f6c453;
    border-radius: 10px;
    border: 1px solid rgba(239, 233, 223, 0.2);
    box-shadow: none;
    outline: none;
    min-height: 32px;
}
.bubble entry:focus-within { border-color: #f6c453; }
.bubble button {
    background: rgba(239, 233, 223, 0.08);
    color: #efe9df;
    border: none;
    border-radius: 8px;
    box-shadow: none;
    padding: 2px 10px;
    min-height: 24px;
}
.bubble button:hover { background: rgba(246, 196, 83, 0.25); }
.bubble button.close { background: none; padding: 0 6px; min-width: 0; }
"""


class Bubble:
    def __init__(
        self,
        app: Gtk.Application,
        config: MascotConfig,
        mascot_height: int,
        on_submit: Callable[[str], None],
        on_close: Callable[[], None],
    ) -> None:
        self.on_submit = on_submit
        self.on_close = on_close
        self.exchanges: list[tuple[str, str]] = []  # (question, réponse) affichés
        self.thinking = False

        self.window = Gtk.Window(application=app, title="Custos", decorated=False)
        self.window.add_css_class("custos-bubble")
        LayerShell.init_for_window(self.window)
        LayerShell.set_namespace(self.window, "custos-bubble")
        LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
        self._set_keyboard(False)
        vertical, horizontal = config.position.split("-")
        edges = {
            "top": LayerShell.Edge.TOP,
            "bottom": LayerShell.Edge.BOTTOM,
            "left": LayerShell.Edge.LEFT,
            "right": LayerShell.Edge.RIGHT,
        }
        LayerShell.set_anchor(self.window, edges[vertical], True)
        LayerShell.set_anchor(self.window, edges[horizontal], True)
        LayerShell.set_margin(self.window, edges[vertical], config.margin + mascot_height)
        LayerShell.set_margin(self.window, edges[horizontal], config.margin)

        self._build()

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.window.add_controller(keys)

    # --- construction -------------------------------------------------------

    def _build(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("bubble")
        box.set_size_request(WIDTH, -1)

        header = Gtk.Box(spacing=8)
        title = Gtk.Label(label="Custos", xalign=0)
        title.add_css_class("title")
        self.summary = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.summary.add_css_class("summary")
        close = Gtk.Button(label="✕", tooltip_text="Fermer (Échap)")
        close.add_css_class("close")
        close.connect("clicked", lambda *_: self.close())
        header.append(title)
        header.append(self.summary)
        header.append(close)

        self.text = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
        self.text.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.text.set_max_width_chars(1)  # la largeur vient de la bulle, pas du texte
        self.text.set_focusable(False)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(MAX_HEIGHT)
        self.scroll.set_child(self.text)

        self.error = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.error.add_css_class("error")

        self.entry = Gtk.Entry(placeholder_text="Demande à Custos…", hexpand=True)
        self.entry.connect("activate", self._on_activate)
        self.copy_button = Gtk.Button(label="Copier", visible=False)
        self.copy_button.connect("clicked", self._on_copy)
        footer = Gtk.Box(spacing=8)
        footer.append(self.entry)
        footer.append(self.copy_button)

        box.append(header)
        box.append(self.scroll)
        box.append(self.error)
        box.append(footer)
        self.window.set_child(box)

    # --- API utilisée par la mascotte --------------------------------------

    @property
    def visible(self) -> bool:
        return self.window.get_visible()

    def open_prompt(self, summary: str, hint: str) -> None:
        """Nouvelle conversation : bulle vide, champ de saisie prêt."""
        self.exchanges = []
        self.thinking = False
        self.summary.set_text(summary)
        self._render()
        self._show_error("")
        self.copy_button.set_visible(False)
        self.entry.set_sensitive(True)
        self.entry.set_text("")
        self.entry.set_placeholder_text(hint)
        self._set_keyboard(True)
        self.window.present()
        self.entry.grab_focus()

    def start_answer(self, question: str) -> None:
        if not self.visible:
            # Question venue de `custos ask "…"` : la bulle s'ouvre directement sur la réponse.
            self.exchanges = []
            self.summary.set_text("")
            self.window.present()
        self.thinking = True
        self.exchanges.append((question, ""))
        self.entry.set_text("")
        self.entry.set_sensitive(False)
        self.copy_button.set_visible(False)
        self._show_error("")
        self._render()

    def append_answer(self, text: str) -> None:
        if not self.exchanges:
            return
        question, answer = self.exchanges[-1]
        self.exchanges[-1] = (question, answer + text)
        self._render()

    def end_answer(self, answer: str) -> None:
        self.thinking = False
        if self.exchanges:
            self.exchanges[-1] = (self.exchanges[-1][0], answer)
        self._render()
        self.entry.set_sensitive(True)
        self.entry.set_placeholder_text("Une autre question ?")
        self.copy_button.set_label("Copier la commande" if markdown.code_blocks(answer) else "Copier")
        self.copy_button.set_visible(True)
        # La réponse reste affichée sans bloquer le clavier : un clic sur la bulle le reprend.
        self._set_keyboard(False)

    def show_error(self, text: str) -> None:
        self.thinking = False
        if self.exchanges and not self.exchanges[-1][1]:
            self.exchanges.pop()  # question restée sans réponse
            self._render()
        self.entry.set_sensitive(True)
        self._show_error(text)
        if not self.visible:
            self.window.present()

    def close(self) -> None:
        if not self.visible:
            return
        self.window.set_visible(False)
        self._set_keyboard(False)
        self.exchanges = []
        self.on_close()

    # --- interne -------------------------------------------------------------

    def _set_keyboard(self, exclusive: bool) -> None:
        # EXCLUSIVE pour taper dès l'ouverture ; ON_DEMAND ensuite pour ne pas
        # confisquer le clavier pendant qu'on lit la réponse.
        mode = LayerShell.KeyboardMode.EXCLUSIVE if exclusive else LayerShell.KeyboardMode.ON_DEMAND
        LayerShell.set_keyboard_mode(self.window, mode)

    def _show_error(self, text: str) -> None:
        self.error.set_text(text)
        self.error.set_visible(bool(text))

    def _render(self) -> None:
        blocks = []
        for question, answer in self.exchanges:
            blocks.append(
                f'<span foreground="{QUESTION_COLOR}"><b>›</b> {GLib.markup_escape_text(question)}</span>'
            )
            if answer:
                blocks.append(markdown.to_pango(answer.strip()))
        if self.thinking and not (self.exchanges and self.exchanges[-1][1]):
            blocks.append("<i>Je réfléchis…</i>")

        markup = "\n\n".join(blocks)
        try:
            Pango.parse_markup(markup, -1, "\0")
            self.text.set_markup(markup)
        except GLib.Error:
            # Markdown inhabituel qui donne un balisage invalide : on affiche le texte brut.
            self.text.set_text("\n\n".join(f"› {q}\n\n{a}" for q, a in self.exchanges))
        self.scroll.set_visible(bool(blocks))
        GLib.idle_add(self._fit_and_scroll)

    def _fit_and_scroll(self) -> bool:
        # Une fenêtre GTK ne suit pas d'elle-même la taille naturelle de son contenu
        # après le premier affichage : on impose la hauteur du texte (plafonnée),
        # puis on redemande la plus petite taille possible pour qu'elle rétrécisse aussi.
        width = self.text.get_width() or WIDTH - 28
        _minimum, natural, _, _ = self.text.measure(Gtk.Orientation.VERTICAL, width)
        self.scroll.set_min_content_height(min(natural, MAX_HEIGHT))
        self.window.set_default_size(-1, -1)
        self.window.queue_resize()
        adjustment = self.scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_upper())
        return GLib.SOURCE_REMOVE

    def _on_activate(self, entry: Gtk.Entry) -> None:
        if self.thinking:
            return
        self.on_submit(entry.get_text())

    def _on_copy(self, *_args) -> None:
        if not self.exchanges:
            return
        answer = self.exchanges[-1][1]
        blocks = markdown.code_blocks(answer)
        if clipboard.copy(blocks[-1] if blocks else answer):
            self.copy_button.set_label("Copié ✓")

    def _on_key(self, _controller, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
