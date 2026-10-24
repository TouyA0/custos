"""Bulle : une petite carte blanche, minimaliste, dans la police du terminal.

Une ligne pour demander, puis seulement la dernière réponse. ＋ démarre une nouvelle
conversation, ↗ ouvre la conversation complète en grand (Claude Code dans un terminal).
Un clic n'importe où en dehors de la carte la ferme ; un travail en cours continue alors
en fond (■ l'arrête). Pendant qu'il agit, une ligne montre l'étape en cours, et une
action sensible attend ici ton accord. Taper « / » propose des commandes (voir slash.py) :
`/h` liste les conversations passées, ↑ ↓ et Entrée en rouvrent une.

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

from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango
from gi.repository import Gtk4LayerShell as LayerShell

from custos.context import clipboard
from custos.ui import markdown, slash
from custos.ui.position import Placement
from custos.ui.theme import LIGHT_THEME, Theme, mix, rgba

WIDTH = 400
MAX_HEIGHT = 360
COPIED_FEEDBACK = 1500  # ms d'affichage du ✓ après une copie
FADE = 120  # ms : fondu de la carte à l'ouverture et à la fermeture
RENDER_INTERVAL = 120  # ms : pendant l'arrivée d'une réponse, on redessine au plus ~8 fois/s
FONT = "JetBrains Mono Nerd Font"  # la police du terminal (foot)

def style(theme: Theme, font: str = FONT) -> str:
    """CSS de la carte, aux couleurs du thème (voir theme.py).

    Pas de flou : une règle de flou Hyprland sur cette carte a coïncidé avec des gels de
    l'affichage (Hyprland 0.56). Fond opaque pour rester lisible sans lui.
    """
    text, error = theme.text, theme.error
    return f"""
/* Pas totalement transparent : Hyprland n'envoie pas la souris à une surface à 0 %. */
window.custos-bubble {{ background-color: rgba(0, 0, 0, 0.01); }}
.card {{
    background-color: {theme.card};  /* opaque : rien ne transparaît derrière le texte */
    border: 1px solid {rgba(theme.border, 0.7)};
    border-radius: 14px;
    padding: 8px 10px 12px 14px;
    font-family: "{font}", monospace;
    font-size: 10pt;
    transition: opacity {FADE}ms ease-out;
}}
.card.hidden {{ opacity: 0; }}
.card label {{ color: {text}; }}
.card .context {{ font-size: 0.85em; color: {rgba(text, 0.5)}; }}
.card .question {{ color: {rgba(text, 0.55)}; }}
.card .thinking {{ color: {rgba(text, 0.5)}; }}
.card .error {{ color: {error}; }}
.card .permission {{
    background-color: {rgba(error, 0.07)};
    border: 1px solid {rgba(error, 0.25)};
    border-radius: 10px;
    padding: 8px 10px;
}}
.card .permission .title {{ color: {error}; font-size: 0.9em; }}
.card .permission .action {{ font-size: 0.95em; }}
.card .permission button {{
    background: {rgba(text, 0.06)};
    border: none;
    box-shadow: none;
    border-radius: 8px;
    color: {text};
    padding: 2px 10px;
    min-height: 24px;
}}
.card .permission button:hover {{ background: {rgba(text, 0.12)}; }}
.card .permission button.allow {{ background: {theme.accent}; }}
.card .permission button.allow label {{ color: {theme.on_accent}; }}  /* sinon « .card label » l'emporte */
.card .permission button.allow:hover {{ background: {mix(theme.accent, theme.on_accent, 0.15)}; }}
.card scrolledwindow.code {{ background-color: {rgba(text, 0.07)}; border-radius: 8px; }}
.card label.code {{ padding: 8px 32px 8px 10px; }}
.card .codeblock.command:hover scrolledwindow.code {{ background-color: {rgba(text, 0.12)}; }}
.card label.table {{ padding: 2px 0 6px 0; }}
.card label link, .card label link:visited {{ color: {theme.accent}; }}
.card .divider {{ background-color: {rgba(text, 0.1)}; min-height: 1px; }}
.card button.icon {{
    background: none;
    border: none;
    box-shadow: none;
    border-radius: 8px;
    color: {rgba(text, 0.55)};
    font-size: 1.15em;
    min-width: 28px;
    min-height: 28px;
    padding: 0;
}}
.card button.icon:hover {{ background-color: {rgba(text, 0.08)}; color: {text}; }}
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
    color: {text};
    caret-color: {text};
    padding: 0;
    min-height: 24px;
}}
.card entry placeholder {{ color: {rgba(text, 0.4)}; }}
.card list.suggestions {{ background: transparent; }}
.card list.suggestions row {{ border-radius: 8px; padding: 3px 6px; }}
.card list.suggestions row:selected {{ background-color: {rgba(text, 0.09)}; }}
.card list.suggestions .when {{ color: {rgba(text, 0.45)}; }}
.card list.suggestions .empty {{ color: {rgba(text, 0.45)}; }}
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
        on_stop: Callable[[], None] = lambda: None,
        on_permission: Callable[[str], None] = lambda choice: None,
        on_history: Callable[[str], list[tuple[str, str, str]]] = lambda query: [],
        on_open_history: Callable[[str], None] = lambda session: None,
        on_run: Callable[[str], None] = lambda command: None,
    ) -> None:
        self.on_run = on_run  # ouvre une commande de la réponse dans un terminal
        self.theme = LIGHT_THEME  # couleurs du code dans le texte (le reste : la feuille de style)
        self.on_history = on_history  # (session, quand, titre) des conversations trouvées
        self.on_open_history = on_open_history
        self._suggestions: list[tuple[str, str]] = []  # (action, valeur) de chaque ligne
        self.on_stop = on_stop
        self.on_permission = on_permission
        self.status = ""  # étape en cours, pendant qu'il agit
        self.permission = ""  # action qui attend ton accord
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
        # Avant le champ de saisie : ↑ ↓ servent à choisir dans la liste des commandes.
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
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
        self.stop_button = _icon_button("■", "Arrêter", lambda *_: self.on_stop())
        header.append(self.info)
        header.append(self.stop_button)
        header.append(self.new_button)
        header.append(self.open_button)
        header.append(_icon_button("✕", "Fermer (Échap)", lambda *_: self.close()))

        self.blocks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, visible=False)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(MAX_HEIGHT)
        self.scroll.set_child(self.blocks)
        self.scroll.set_margin_end(4)

        self.thinking_label = Gtk.Label(xalign=0, visible=False, ellipsize=Pango.EllipsizeMode.END)
        self.thinking_label.set_max_width_chars(1)
        self.thinking_label.add_css_class("thinking")
        self.permission_box = self._permission_widget()
        self.error = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.error.set_max_width_chars(1)
        self.error.add_css_class("error")

        self.divider = Gtk.Box(visible=False)
        self.divider.add_css_class("divider")
        self.divider.set_margin_end(4)
        self.entry = Gtk.Entry(placeholder_text="Demande à Custos…", hexpand=True)
        self.entry.set_margin_end(4)
        self.entry.connect("activate", self._on_activate)
        self.entry.connect("changed", lambda _entry: self._update_suggestions())

        self.suggestions = Gtk.ListBox(visible=False, selection_mode=Gtk.SelectionMode.BROWSE)
        self.suggestions.add_css_class("suggestions")
        self.suggestions.set_margin_end(4)
        self.suggestions.connect("row-activated", lambda _list, row: self._pick(row.get_index()))

        for widget in (
            header,
            self.scroll,
            self.permission_box,
            self.thinking_label,
            self.error,
            self.divider,
            self.entry,
            self.suggestions,
        ):
            card.append(widget)
        card.set_halign(Gtk.Align.START)
        self.card = card
        self.window.set_child(card)

    def _permission_widget(self) -> Gtk.Widget:
        """Action sensible : ce qu'il veut faire, en entier, et tes trois réponses."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False)
        box.add_css_class("permission")
        title = Gtk.Label(label="⚠ Action sensible : je fais ça ?", xalign=0)
        title.add_css_class("title")
        self.permission_label = Gtk.Label(xalign=0, wrap=True, selectable=True, focusable=False)
        self.permission_label.set_wrap_mode(Pango.WrapMode.CHAR)
        self.permission_label.set_max_width_chars(1)
        self.permission_label.add_css_class("action")
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        for label, choice, tooltip in (
            ("Refuser", "deny", "Il ne le fait pas et cherche une autre solution"),
            ("Toujours", "always", "Autoriser, et ne plus redemander dans cette conversation"),
            ("Autoriser", "allow", "Autoriser cette fois"),
        ):
            button = Gtk.Button(label=label, tooltip_text=tooltip)
            button.add_css_class(choice)
            button.connect("clicked", lambda _b, choice=choice: self._answer_permission(choice))
            buttons.append(button)
        for widget in (title, self.permission_label, buttons):
            box.append(widget)
        return box

    def _text_widget(self, text: str) -> Gtk.Widget:
        label = Gtk.Label(xalign=0, wrap=True, selectable=True, focusable=False, hexpand=True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_max_width_chars(1)  # la largeur vient de la carte, pas du texte
        markup = markdown.to_pango(text, self.theme.text, self.theme.code)
        if markdown.valid(markup):
            label.set_markup(markup)
        else:
            label.set_text(text)  # Markdown inhabituel : texte brut plutôt que rien
        label.connect("activate-link", self._on_link)
        return label

    def _table_widget(self, table: str) -> Gtk.Widget:
        # Comme le code : pas de retour à la ligne, un tableau large défile horizontalement.
        label = Gtk.Label(xalign=0, yalign=0, selectable=True, focusable=False)
        label.add_css_class("table")
        markup = markdown.table_markup(table, self.theme.text, self.theme.code)
        if markdown.valid(markup):
            label.set_markup(markup)
        else:
            label.set_text(table)
        label.connect("activate-link", self._on_link)
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.NEVER
        )
        scroller.set_propagate_natural_height(True)
        scroller.set_child(label)
        scroller.add_css_class("table")
        return scroller

    def _on_link(self, _label: Gtk.Label, uri: str) -> bool:
        """Lien de la réponse : ouvert dans le navigateur, et la carte se ferme pour le laisser voir."""
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error:
            return False  # GTK essaie à sa façon
        self.close()
        return True

    def _code_widget(self, code: str, command: bool = False) -> Gtk.Widget:
        # Pas de retour à la ligne : une longue ligne défile horizontalement, comme dans
        # un terminal, plutôt que d'être coupée en plein mot. Une commande s'ouvre d'un clic
        # dans un terminal (le clic ne sert donc pas à la sélectionner : ⧉ la copie).
        label = Gtk.Label(label=code, xalign=0, yalign=0, selectable=not command, focusable=False)
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
        if command:
            block.add_css_class("command")
            scroller.set_cursor_from_name("pointer")
            scroller.set_tooltip_text("Ouvrir dans un terminal, prête à lancer")
            click = Gtk.GestureClick()
            click.connect("released", lambda *_args: self._run(code))
            scroller.add_controller(click)
        return block

    def _run(self, code: str) -> None:
        """Clic sur une commande : un terminal s'ouvre avec elle tapée, et la carte se ferme."""
        self.on_run(markdown.runnable(code))
        self.close()

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

    def refresh(self) -> None:
        """Nouveau thème : le code dans le texte reprend ses couleurs (balisage Pango)."""
        self._render(from_top=not self.thinking)

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
        self.status, self.permission = "", ""
        self.answer = answer
        self.entry.set_placeholder_text("Répondre…")
        self._render(from_top=True)

    def set_status(self, text: str) -> None:
        """Étape en cours (« ▶ journalctl -b -1 »), à côté des points d'attente."""
        self.status = text
        self._render()

    def reset_answer(self) -> None:
        """Le texte reçu n'était qu'un commentaire avant une étape : on ne garde que la conclusion."""
        self.answer = ""
        self._render()

    def ask_permission(self, description: str) -> None:
        self.permission = description
        self._render()

    def permission_done(self) -> None:
        self.permission = ""
        self._render()

    def show_progress(self, question: str, status: str, answer: str, permission: str) -> None:
        """Carte rouverte pendant qu'il travaille : là où il en est."""
        self.question, self.status, self.answer, self.permission = question, status, answer, permission
        self.summary = ""
        self._show_error("")
        self._set_thinking(True)
        self._render()
        self._show()

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
        self.question, self.answer, self.status, self.permission = "", "", "", ""
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
        self.thinking_label.set_text(self._thinking_text())
        return GLib.SOURCE_CONTINUE

    def _thinking_text(self) -> str:
        dots = THINKING_FRAMES[self._thinking_frame]
        return f"{dots}  {self.status}" if self.status else dots

    def _answer_permission(self, choice: str) -> None:
        self.permission = ""
        self._render()
        self.on_permission(choice)

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
        self.stop_button.set_visible(self.thinking)
        self.permission_label.set_text(self.permission)
        self.permission_box.set_visible(bool(self.permission))

        while child := self.blocks.get_first_child():
            self.blocks.remove(child)
        for kind, content in markdown.blocks(self.answer.strip()):
            if kind in ("code", "command"):
                self.blocks.append(self._code_widget(content, command=kind == "command"))
            elif kind == "table":
                self.blocks.append(self._table_widget(content))
            else:
                self.blocks.append(self._text_widget(content))
        if self.thinking and self.suggestions.get_visible():
            self.suggestions.set_visible(False)  # le champ disparaît : sa liste aussi
        listing = self.suggestions.get_visible()
        self.scroll.set_visible(bool(self.answer.strip()) and not listing)

        self.thinking_label.set_text(self._thinking_text())
        self.thinking_label.set_visible(self.thinking and not self.answer and not self.permission)
        # Pendant la réponse, pas de champ : il revient (« Répondre… ») une fois qu'elle est finie.
        self.entry.set_visible(not self.thinking)
        self.divider.set_visible(bool(self.answer) and not self.thinking and not listing)
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
        if self.suggestions.get_visible():
            row = self.suggestions.get_selected_row()
            if row is not None:
                self._pick(row.get_index())
            return
        parsed = slash.parse(entry.get_text())
        if parsed and parsed[0].action in (slash.NEW, slash.OPEN):
            self._run(parsed[0].action)
            return
        self.on_submit(entry.get_text())  # `/vite …` compris : c'est une question (voir daemon)

    # --- commandes « / » ----------------------------------------------------------

    def _update_suggestions(self) -> None:
        """Sous le champ : les commandes possibles, ou les conversations de `/h …`."""
        text = self.entry.get_text()
        rows: list[tuple[str, str, str]] = []  # (à gauche, texte, mention discrète)
        self._suggestions = []
        parsed = slash.parse(text)
        if parsed and parsed[0].action == slash.HISTORY:
            for session, when, title in self.on_history(parsed[1]):
                rows.append((when, title, ""))
                self._suggestions.append(("conversation", session))
            if not rows:
                rows.append(("", "Aucune conversation trouvée", "empty"))
                self._suggestions.append(("none", ""))
        else:
            for command in slash.completions(text):
                rows.append((f"/{command.alias}", f"/{command.name}  {command.description}", ""))
                self._suggestions.append(("command", command.action))

        while child := self.suggestions.get_first_child():
            self.suggestions.remove(child)
        for left, label, css in rows:
            self.suggestions.append(self._suggestion_row(left, label, css))
        self.suggestions.set_visible(bool(rows))
        if rows:
            self.suggestions.select_row(self.suggestions.get_row_at_index(0))
        # La liste remplace la réponse affichée, le temps de choisir.
        self.scroll.set_visible(not rows and bool(self.answer.strip()))
        self.divider.set_visible(not rows and bool(self.answer) and not self.thinking)

    def _suggestion_row(self, left: str, label: str, css: str) -> Gtk.Widget:
        box = Gtk.Box(spacing=10)
        if left:
            when = Gtk.Label(label=left, xalign=0)
            when.add_css_class("when")
            box.append(when)
        text = Gtk.Label(label=" ".join(label.split()), xalign=0, hexpand=True)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.set_max_width_chars(1)
        if css:
            text.add_css_class(css)
        box.append(text)
        return box

    def _pick(self, index: int) -> None:
        if not 0 <= index < len(self._suggestions):
            return
        kind, value = self._suggestions[index]
        if kind == "conversation":
            self.entry.set_text("")
            self.on_open_history(value)
        elif kind == "command":
            self._run(value)

    def _run(self, action: str) -> None:
        if action in (slash.HISTORY, slash.FAST):
            # Choisie dans la liste : la commande s'écrit, la suite est à taper.
            self.entry.set_text("/h " if action == slash.HISTORY else "/vite ")
            self.entry.set_position(-1)
            return
        self.entry.set_text("")
        if action == slash.NEW:
            self.on_new()
        elif action == slash.OPEN:
            self.on_open_full()

    def _move_selection(self, step: int) -> None:
        row = self.suggestions.get_selected_row()
        index = (row.get_index() if row else -1) + step
        target = self.suggestions.get_row_at_index(max(0, min(index, len(self._suggestions) - 1)))
        if target:
            self.suggestions.select_row(target)

    def _on_copy(self, button: Gtk.Button, code: str) -> None:
        if clipboard.copy(code):
            button.set_label("✓")
            GLib.timeout_add(COPIED_FEEDBACK, lambda: button.set_label("⧉") or GLib.SOURCE_REMOVE)

    def _on_key(self, _controller, keyval: int, _keycode: int, _state: Gdk.ModifierType) -> bool:
        listing = self.suggestions.get_visible()
        if keyval == Gdk.KEY_Escape:
            if listing:
                self.entry.set_text("")  # Échap efface la commande ; une deuxième fois, ferme
            else:
                self.close()
            return True
        if listing and keyval in (Gdk.KEY_Up, Gdk.KEY_Down):
            self._move_selection(-1 if keyval == Gdk.KEY_Up else 1)
            return True
        return False
