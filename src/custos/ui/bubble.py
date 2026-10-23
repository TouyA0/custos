"""Bulle : une petite carte blanche, minimaliste, dans la police du terminal.

Une ligne pour demander, puis seulement la dernière réponse. ＋ démarre une nouvelle
conversation, ↗ ouvre la conversation complète en grand (Claude Code dans un terminal).
Un clic n'importe où en dehors de la carte la ferme.

La carte vit dans une surface layer-shell plein écran presque invisible : un clic sur
cette surface, hors de la carte, la ferme. (Une surface séparée pour attraper ces clics
ne marche pas : quand la carte a le clavier en exclusif, Hyprland lui réserve aussi la
souris.) La carte peut ainsi changer de taille sans redimensionner de fenêtre.
Toutes les méthodes s'exécutent dans le thread GTK.
"""

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Pango", "1.0")
gi.require_version("Gtk4LayerShell", "1.0")

from gi.repository import Gdk, GLib, Graphene, Gtk, Pango
from gi.repository import Gtk4LayerShell as LayerShell

from custos.context import clipboard
from custos.ui import markdown
from custos.ui.position import Placement

WIDTH = 400
MAX_HEIGHT = 360
COPIED_FEEDBACK = 1500  # ms d'affichage du ✓ après une copie
FADE = 120  # ms : fondu de la carte à l'ouverture et à la fermeture
RENDER_INTERVAL = 120  # ms : pendant l'arrivée d'une réponse, on redessine au plus ~8 fois/s
FONT = "JetBrains Mono Nerd Font"  # la police du terminal (foot)

# Pas de flou : une règle de flou Hyprland sur cette carte a coïncidé avec des gels de
# l'affichage (Hyprland 0.56). Fond blanc presque opaque pour rester lisible sans lui.
CSS = f"""
/* Pas totalement transparent : Hyprland n'envoie pas la souris à une surface à 0 %. */
window.custos-bubble {{ background-color: rgba(0, 0, 0, 0.01); }}
.card {{
    background-color: rgba(248, 248, 250, 0.97);
    border: 1px solid rgba(255, 255, 255, 0.7);
    border-radius: 14px;
    padding: 8px 10px 12px 14px;
    font-family: "{FONT}", monospace;
    font-size: 10pt;
    transition: opacity {FADE}ms ease-out;
}}
.card.hidden {{ opacity: 0; }}
.card label {{ color: #1a1f2b; }}
.card .context {{ font-size: 0.85em; color: rgba(26, 31, 43, 0.5); }}
.card .question {{ color: rgba(26, 31, 43, 0.55); }}
.card .thinking {{ color: rgba(26, 31, 43, 0.5); }}
.card .error {{ color: #b3402f; }}
.card scrolledwindow.code {{ background-color: rgba(26, 31, 43, 0.07); border-radius: 8px; }}
.card label.code {{ padding: 8px 32px 8px 10px; }}
.card .divider {{ background-color: rgba(26, 31, 43, 0.1); min-height: 1px; }}
.card button.icon {{
    background: none;
    border: none;
    box-shadow: none;
    border-radius: 8px;
    color: rgba(26, 31, 43, 0.55);
    font-size: 1.15em;
    min-width: 28px;
    min-height: 28px;
    padding: 0;
}}
.card button.icon:hover {{ background-color: rgba(26, 31, 43, 0.08); color: #1a1f2b; }}
.card .codeblock button.copy {{
    font-size: 1em;
    min-width: 24px;
    min-height: 24px;
    margin: 4px;
    opacity: 0;
    transition: opacity 120ms ease-out;
}}
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
    button = Gtk.Button(label=label, tooltip_text=tooltip, valign=Gtk.Align.CENTER)
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
        on_new: Callable[[], None],
    ) -> None:
        self.on_submit = on_submit
        self.on_close = on_close
        self.on_open_full = on_open_full
        self.on_new = on_new
        self.question = ""
        self.answer = ""
        self.summary = ""
        self.thinking = False
        self._thinking_frame = 0
        self._thinking_timer = 0
        self._render_pending = 0
        self._height: tuple[bool, int] | None = None
        self._placement: Placement | None = None
        self._fade_timer = 0

        self.window = Gtk.Window(application=app, title="Custos", decorated=False)
        self.window.add_css_class("custos-bubble")
        LayerShell.init_for_window(self.window)
        LayerShell.set_namespace(self.window, "custos-bubble")
        LayerShell.set_layer(self.window, LayerShell.Layer.OVERLAY)
        # Le clavier reste sur la carte tant qu'elle est ouverte (Échap, réponse directe) :
        # pas de piège, puisqu'un clic en dehors la ferme.
        LayerShell.set_keyboard_mode(self.window, LayerShell.KeyboardMode.EXCLUSIVE)
        LayerShell.set_exclusive_zone(self.window, -1)  # coordonnées de l'écran entier
        for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self.window, edge, True)

        self._build()
        click = Gtk.GestureClick(button=0)  # n'importe quel bouton
        click.connect("pressed", self._on_press)
        self.window.add_controller(click)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.window.add_controller(keys)

    # --- construction -------------------------------------------------------

    def _build(self) -> None:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("card")
        card.set_size_request(WIDTH, -1)

        # En-tête : ta question (ou ce que Custos a lu), à gauche ; ＋ ↗ ✕ à droite.
        header = Gtk.Box(spacing=2)
        self.info = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.info.set_max_width_chars(1)  # sinon un texte tronqué réclame quand même toute sa largeur
        self.new_button = _icon_button("＋", "Nouvelle conversation", lambda *_: self.on_new())
        self.open_button = _icon_button(
            "↗", "Ouvrir la conversation en grand", lambda *_: self.on_open_full()
        )
        header.append(self.info)
        header.append(self.new_button)
        header.append(self.open_button)
        header.append(_icon_button("✕", "Fermer (Échap)", lambda *_: self.close()))

        self.blocks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, visible=False)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(MAX_HEIGHT)
        self.scroll.set_child(self.blocks)
        self.scroll.set_margin_end(4)

        self.thinking_label = Gtk.Label(xalign=0, visible=False)
        self.thinking_label.add_css_class("thinking")
        self.error = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.error.set_max_width_chars(1)
        self.error.add_css_class("error")

        self.divider = Gtk.Box(visible=False)
        self.divider.add_css_class("divider")
        self.divider.set_margin_end(4)
        self.entry = Gtk.Entry(placeholder_text="Demande à Custos…", hexpand=True)
        self.entry.set_margin_end(4)
        self.entry.connect("activate", self._on_activate)

        for widget in (header, self.scroll, self.thinking_label, self.error, self.divider, self.entry):
            card.append(widget)
        card.set_halign(Gtk.Align.START)
        self.card = card
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
        copy.set_valign(Gtk.Align.START)
        block = Gtk.Overlay(child=scroller)
        block.add_overlay(copy)
        block.add_css_class("codeblock")
        return block

    # --- API utilisée par la mascotte --------------------------------------

    def set_monitor(self, monitor: Gdk.Monitor) -> None:
        """Écran de la carte : toujours celui de la mascotte."""
        LayerShell.set_monitor(self.window, monitor)

    def place(self, placement: Placement) -> None:
        """Retient où poser la carte (voir position.bubble_placement) ; appliqué à l'ouverture,
        ou tout de suite si elle est ouverte. Rien à faire tant qu'elle est cachée."""
        self._placement = placement
        if self.visible:
            self._apply_placement()

    @property
    def visible(self) -> bool:
        return self.window.get_visible()

    def open_prompt(self, summary: str, hint: str) -> None:
        """Nouvelle conversation : juste une ligne pour demander."""
        self.question, self.answer, self.summary = "", "", summary
        self._set_thinking(False)
        self._show_error("")
        self.entry.set_text("")
        self.entry.set_placeholder_text(hint)
        self._render()
        self._show()

    def reopen(self, summary: str, question: str, answer: str) -> None:
        """Conversation reprise : la dernière réponse, prête pour une question de suivi."""
        self.question, self.answer, self.summary = question, answer, summary
        self._set_thinking(False)
        self._show_error("")
        self.entry.set_text("")
        self.entry.set_placeholder_text("Répondre…")
        self._render(from_top=True)
        self._show()

    def start_answer(self, question: str) -> None:
        if not self.visible:
            # Question venue de `custos ask "…"` : la carte s'ouvre directement sur la réponse.
            self.summary = ""
        # Une question de suivi remplace la réponse affichée : la conversation complète
        # reste dans la session Claude, à ouvrir en grand avec ↗.
        self.question, self.answer = question, ""
        self.entry.set_text("")
        self._show_error("")
        self._set_thinking(True)
        self._render()
        self._show()

    def append_answer(self, text: str) -> None:
        self.answer += text
        if not self._render_pending:
            self._render_pending = GLib.timeout_add(RENDER_INTERVAL, self._flush_render)

    def end_answer(self, answer: str) -> None:
        self._set_thinking(False)
        self.answer = answer
        self.entry.set_placeholder_text("Répondre…")
        self._render(from_top=True)

    def show_error(self, text: str) -> None:
        self._set_thinking(False)
        if self.question and not self.answer:
            self.question = ""  # question restée sans réponse
        self._show_error(text)
        self._render()
        self._show()

    def close(self) -> None:
        if not self.visible or self._fade_timer:
            return
        # Fondu, puis la surface disparaît vraiment (elle bloquerait sinon les clics).
        self.card.add_css_class("hidden")
        self._fade_timer = GLib.timeout_add(FADE, self._hide)
        self._set_thinking(False)
        self._cancel_pending_render()
        self.question, self.answer = "", ""
        self.on_close()

    # --- interne -------------------------------------------------------------

    def _show(self) -> None:
        if self.visible:
            return
        if self._fade_timer:  # rouverte pendant le fondu de fermeture
            GLib.source_remove(self._fade_timer)
            self._fade_timer = 0
        self._apply_placement()
        self.card.add_css_class("hidden")
        self.window.present()
        self.entry.grab_focus()
        GLib.idle_add(lambda: self.card.remove_css_class("hidden") or GLib.SOURCE_REMOVE)

    def _hide(self) -> bool:
        self._fade_timer = 0
        self.window.set_visible(False)
        self.card.remove_css_class("hidden")
        return GLib.SOURCE_REMOVE

    def _on_press(self, _gesture, _n_press: int, x: float, y: float) -> None:
        """Clic sur la surface plein écran : hors de la carte, on ferme."""
        found, bounds = self.card.compute_bounds(self.window)
        if found and not bounds.contains_point(Graphene.Point().init(x, y)):
            self.close()

    def _apply_placement(self) -> None:
        if not self._placement:
            return
        # Au-dessus de la mascotte, la carte est collée en bas de sa zone : elle grandit
        # vers le haut sans jamais la recouvrir.
        top = self._placement.edge == "top"
        self.card.set_margin_start(self._placement.left)
        self.card.set_valign(Gtk.Align.START if top else Gtk.Align.END)
        self.card.set_margin_top(self._placement.margin if top else 0)
        self.card.set_margin_bottom(0 if top else self._placement.margin)

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

    def _flush_render(self) -> bool:
        self._render_pending = 0
        self._render()
        return GLib.SOURCE_REMOVE

    def _cancel_pending_render(self) -> None:
        if self._render_pending:
            GLib.source_remove(self._render_pending)
            self._render_pending = 0

    def _render(self, from_top: bool = False) -> None:
        """Redessine la carte. Pendant l'arrivée de la réponse, on suit sa fin ; une fois
        terminée (`from_top`), on revient à son début pour la lire dans l'ordre."""
        self._cancel_pending_render()
        if self.question:
            self.info.set_text(f"› {self.question}")
            self.info.remove_css_class("context")
            self.info.add_css_class("question")
        else:
            self.info.set_text(self.summary)
            self.info.remove_css_class("question")
            self.info.add_css_class("context")
        has_answer = bool(self.answer.strip()) and not self.thinking
        self.new_button.set_visible(has_answer)
        self.open_button.set_visible(has_answer)

        while child := self.blocks.get_first_child():
            self.blocks.remove(child)
        for kind, content in markdown.blocks(self.answer.strip()):
            self.blocks.append(self._code_widget(content) if kind == "code" else self._text_widget(content))
        self.scroll.set_visible(bool(self.answer.strip()))

        self.thinking_label.set_text(THINKING_FRAMES[self._thinking_frame])
        self.thinking_label.set_visible(self.thinking and not self.answer)
        # Pendant la réponse, pas de champ : il revient (« Répondre… ») une fois qu'elle est finie.
        self.entry.set_visible(not self.thinking)
        self.divider.set_visible(bool(self.answer) and not self.thinking)
        if not self.thinking:
            self.entry.grab_focus()
        GLib.idle_add(self._fit_and_scroll, from_top)

    def _fit_and_scroll(self, from_top: bool) -> bool:
        # Hauteur de la zone de réponse : naturelle, plafonnée à MAX_HEIGHT (avec défilement).
        width = self.blocks.get_width() or WIDTH - 28
        _minimum, natural, _, _ = self.blocks.measure(Gtk.Orientation.VERTICAL, width)
        long_answer = natural > MAX_HEIGHT
        height = (long_answer, MAX_HEIGHT if long_answer else natural)
        if height != self._height:
            self._height = height
            # Défilement seulement pour une longue réponse : sinon la zone garde une
            # hauteur minimale et laisse un vide sous une réponse d'une ligne.
            self.scroll.set_policy(
                Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC if long_answer else Gtk.PolicyType.NEVER
            )
            self.scroll.set_min_content_height(MAX_HEIGHT if long_answer else -1)
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
