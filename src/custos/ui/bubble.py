"""Bulle : une petite carte en verre blanc (flou de Hyprland derrière), comme le terminal.

Minimaliste : une ligne pour demander, puis seulement la dernière réponse. Le bouton
↗ ouvre la conversation complète en grand (Claude Code dans un terminal).
Fenêtre layer-shell à part, placée au-dessus (ou en dessous) de la mascotte.
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

from custos.context import clipboard
from custos.ui import markdown
from custos.ui.position import Placement

WIDTH = 400
MAX_HEIGHT = 360
COPIED_FEEDBACK = 1500  # ms d'affichage du ✓ après une copie
FONT = "JetBrains Mono Nerd Font"  # la police du terminal (foot)

# Fond blanc légèrement translucide. La règle de flou de integrations/hyprland/custos.lua
# devrait y ajouter un effet de verre dépoli, mais le flou des surfaces layer-shell
# ne prend pas avec Hyprland 0.56 : d'où une opacité assez forte pour rester lisible.
CSS = f"""
window.custos-bubble {{ background: transparent; }}
.card {{
    background-color: rgba(248, 248, 250, 0.92);
    border: 1px solid rgba(255, 255, 255, 0.7);
    border-radius: 14px;
    padding: 10px 14px 12px 14px;
    font-family: "{FONT}", monospace;
    font-size: 10pt;
}}
.card label {{ color: #1a1f2b; }}
.card .context {{ font-size: 0.85em; color: rgba(26, 31, 43, 0.5); }}
.card .question {{ color: rgba(26, 31, 43, 0.55); }}
.card .thinking {{ color: rgba(26, 31, 43, 0.5); }}
.card .error {{ color: #b3402f; }}
.card scrolledwindow.code {{ background-color: rgba(26, 31, 43, 0.07); border-radius: 8px; }}
.card label.code {{ padding: 8px 30px 8px 10px; }}
.card .divider {{ background-color: rgba(26, 31, 43, 0.1); min-height: 1px; }}
.card button.icon {{
    background: none;
    border: none;
    box-shadow: none;
    color: rgba(26, 31, 43, 0.45);
    padding: 0 3px;
    min-height: 0;
    min-width: 0;
}}
.card button.icon:hover {{ color: #1a1f2b; }}
.card .codeblock button.copy {{ margin: 4px; opacity: 0; transition: opacity 120ms ease-out; }}
.card .codeblock:hover button.copy {{ opacity: 1; }}
.card scrolledwindow:not(.code), .card viewport {{ background: transparent; }}
.card entry {{
    background: none;
    border: none;
    box-shadow: none;
    outline: none;
    color: #1a1f2b;
    caret-color: #1a1f2b;
    padding: 0;
    min-height: 24px;
}}
.card entry placeholder {{ color: rgba(26, 31, 43, 0.4); }}
"""

# Trois points dont un s'allume à tour de rôle, comme « en train d'écrire ».
THINKING_FRAMES = ["● ○ ○", "○ ● ○", "○ ○ ●"]


def _icon_button(label: str, tooltip: str, on_click: Callable[..., None], *extra_classes: str) -> Gtk.Button:
    button = Gtk.Button(label=label, tooltip_text=tooltip, valign=Gtk.Align.START)
    button.add_css_class("icon")
    for css_class in extra_classes:
        button.add_css_class(css_class)
    button.connect("clicked", on_click)
    return button


class Bubble:
    def __init__(
        self,
        app: Gtk.Application,
        on_submit: Callable[[str], None],
        on_close: Callable[[], None],
        on_open_full: Callable[[], None],
    ) -> None:
        self.on_submit = on_submit
        self.on_close = on_close
        self.on_open_full = on_open_full
        self.question = ""
        self.answer = ""
        self.thinking = False
        self._thinking_frame = 0
        self._thinking_timer = 0

        self.window = Gtk.Window(application=app, title="Custos", decorated=False)
        self.window.add_css_class("custos-bubble")
        LayerShell.init_for_window(self.window)
        LayerShell.set_namespace(self.window, "custos-bubble")
        LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
        self._set_keyboard(False)
        LayerShell.set_exclusive_zone(self.window, -1)  # coordonnées de l'écran entier
        LayerShell.set_anchor(self.window, LayerShell.Edge.LEFT, True)

        self._build()

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.window.add_controller(keys)

    # --- construction -------------------------------------------------------

    def _build(self) -> None:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("card")
        card.set_size_request(WIDTH, -1)

        # En-tête : ta question (ou ce que Custos a lu), à gauche ; ↗ et ✕ à droite.
        header = Gtk.Box(spacing=4)
        self.info = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.info.set_max_width_chars(1)  # sinon un texte tronqué réclame quand même toute sa largeur
        self.summary = ""
        self.open_button = _icon_button(
            "↗", "Ouvrir la conversation en grand", lambda *_: self.on_open_full()
        )
        self.open_button.set_visible(False)
        header.append(self.info)
        header.append(self.open_button)
        header.append(_icon_button("✕", "Fermer (Échap)", lambda *_: self.close()))

        self.blocks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, visible=False)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(MAX_HEIGHT)
        self.scroll.set_child(self.blocks)

        self.thinking_label = Gtk.Label(xalign=0, visible=False)
        self.thinking_label.add_css_class("thinking")
        self.error = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.error.set_max_width_chars(1)
        self.error.add_css_class("error")

        self.divider = Gtk.Box(visible=False)
        self.divider.add_css_class("divider")
        self.entry = Gtk.Entry(placeholder_text="Demande à Custos…", hexpand=True)
        self.entry.connect("activate", self._on_activate)

        for widget in (header, self.scroll, self.thinking_label, self.error, self.divider, self.entry):
            card.append(widget)
        self.window.set_child(card)

    def _text_widget(self, text: str) -> Gtk.Widget:
        label = Gtk.Label(xalign=0, wrap=True, selectable=True, focusable=False, hexpand=True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(1)  # la largeur vient de la carte, pas du texte
        markup = markdown.to_pango(text)
        try:
            Pango.parse_markup(markup, -1, "\0")
            label.set_markup(markup)
        except GLib.Error:
            label.set_text(text)  # Markdown inhabituel : texte brut plutôt que rien
        return label

    def _code_widget(self, code: str) -> Gtk.Widget:
        # Pas de retour à la ligne : une longue ligne défile horizontalement, comme dans
        # un terminal, plutôt que d'être coupée en plein mot.
        label = Gtk.Label(label=code, xalign=0, yalign=0, selectable=True, focusable=False)
        label.add_css_class("code")
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.NEVER
        )
        scroller.set_propagate_natural_height(True)
        scroller.set_child(label)
        scroller.add_css_class("code")
        copy = _icon_button("⧉", "Copier", lambda button: self._on_copy(button, code), "copy")
        copy.set_halign(Gtk.Align.END)
        block = Gtk.Overlay(child=scroller)
        block.add_overlay(copy)
        block.add_css_class("codeblock")
        return block

    # --- API utilisée par la mascotte --------------------------------------

    def place(self, placement: Placement) -> None:
        """Positionne la bulle à côté de la mascotte (voir position.bubble_placement)."""
        top = placement.edge == "top"
        LayerShell.set_anchor(self.window, LayerShell.Edge.TOP, top)
        LayerShell.set_anchor(self.window, LayerShell.Edge.BOTTOM, not top)
        LayerShell.set_margin(self.window, LayerShell.Edge.LEFT, placement.left)
        edge = LayerShell.Edge.TOP if top else LayerShell.Edge.BOTTOM
        LayerShell.set_margin(self.window, edge, placement.margin)

    @property
    def visible(self) -> bool:
        return self.window.get_visible()

    def open_prompt(self, summary: str, hint: str) -> None:
        """Nouvelle conversation : juste une ligne pour demander."""
        self.question, self.answer = "", ""
        self._set_thinking(False)
        self.summary = summary
        self.open_button.set_visible(False)
        self._show_error("")
        self.entry.set_text("")
        self.entry.set_placeholder_text(hint)
        self._render()
        self._set_keyboard(True)
        self.window.present()
        self.entry.grab_focus()

    def start_answer(self, question: str) -> None:
        if not self.visible:
            # Question venue de `custos ask "…"` : la bulle s'ouvre directement sur la réponse.
            self.summary = ""
            self.open_button.set_visible(False)
            self.window.present()
        # Une question de suivi remplace la réponse affichée : la conversation complète
        # reste dans la session Claude, à ouvrir en grand avec ↗.
        self.question, self.answer = question, ""
        self.entry.set_text("")
        self._show_error("")
        self._set_thinking(True)
        self._render()

    def append_answer(self, text: str) -> None:
        self.answer += text
        self._render()

    def end_answer(self, answer: str) -> None:
        self._set_thinking(False)
        self.answer = answer
        self.open_button.set_visible(True)
        self.entry.set_placeholder_text("Répondre…")
        self._render(from_top=True)
        # La réponse reste affichée sans bloquer le clavier : un clic sur la bulle le reprend.
        self._set_keyboard(False)

    def show_error(self, text: str) -> None:
        self._set_thinking(False)
        if self.question and not self.answer:
            self.question = ""  # question restée sans réponse
        self._show_error(text)
        self._render()
        if not self.visible:
            self.window.present()

    def close(self) -> None:
        if not self.visible:
            return
        self.window.set_visible(False)
        self._set_keyboard(False)
        self._set_thinking(False)
        self.question, self.answer = "", ""
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

    def _set_thinking(self, thinking: bool) -> None:
        self.thinking = thinking
        if thinking and not self._thinking_timer:
            self._thinking_timer = GLib.timeout_add(300, self._animate_thinking)
        elif not thinking and self._thinking_timer:
            GLib.source_remove(self._thinking_timer)
            self._thinking_timer = 0

    def _animate_thinking(self) -> bool:
        self._thinking_frame = (self._thinking_frame + 1) % len(THINKING_FRAMES)
        self.thinking_label.set_text(THINKING_FRAMES[self._thinking_frame])
        return GLib.SOURCE_CONTINUE

    def _render(self, from_top: bool = False) -> None:
        """Redessine la carte. Pendant l'arrivée de la réponse, on suit sa fin ; une fois
        terminée (`from_top`), on revient à son début pour la lire dans l'ordre."""
        if self.question:
            self.info.set_text(f"› {self.question}")
            self.info.remove_css_class("context")
            self.info.add_css_class("question")
        else:
            self.info.set_text(self.summary)
            self.info.remove_css_class("question")
            self.info.add_css_class("context")

        while child := self.blocks.get_first_child():
            self.blocks.remove(child)
        for kind, content in markdown.blocks(self.answer.strip()):
            self.blocks.append(self._code_widget(content) if kind == "code" else self._text_widget(content))
        self.scroll.set_visible(bool(self.answer.strip()))

        waiting = self.thinking and not self.answer
        self.thinking_label.set_text(THINKING_FRAMES[self._thinking_frame])
        self.thinking_label.set_visible(waiting)
        # Pendant la réponse, pas de champ : il revient (« Répondre… ») une fois qu'elle est finie.
        self.entry.set_visible(not self.thinking)
        self.divider.set_visible(bool(self.answer) and not self.thinking)
        GLib.idle_add(self._fit_and_scroll, from_top)

    def _fit_and_scroll(self, from_top: bool) -> bool:
        # Une fenêtre GTK ne suit pas d'elle-même la taille naturelle de son contenu
        # après le premier affichage : on impose la hauteur de la réponse (plafonnée),
        # puis on redemande la plus petite taille possible pour qu'elle rétrécisse aussi.
        width = self.blocks.get_width() or WIDTH - 28
        _minimum, natural, _, _ = self.blocks.measure(Gtk.Orientation.VERTICAL, width)
        # Défilement seulement pour une longue réponse : sinon la zone garde une hauteur
        # minimale et laisse un vide sous une réponse d'une ligne.
        long_answer = natural > MAX_HEIGHT
        self.scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC if long_answer else Gtk.PolicyType.NEVER
        )
        self.scroll.set_min_content_height(MAX_HEIGHT if long_answer else -1)
        self.window.set_default_size(-1, -1)
        self.window.queue_resize()
        adjustment = self.scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_lower() if from_top else adjustment.get_upper())
        return GLib.SOURCE_REMOVE

    def _on_activate(self, entry: Gtk.Entry) -> None:
        if self.thinking:
            return
        self.on_submit(entry.get_text())

    def _on_copy(self, button: Gtk.Button, code: str) -> None:
        if clipboard.copy(code):
            button.set_label("✓")
            GLib.timeout_add(COPIED_FEEDBACK, lambda: button.set_label("⧉") or GLib.SOURCE_REMOVE)

    def _on_key(self, _controller, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
