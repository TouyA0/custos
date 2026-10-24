"""Collecte du contexte envoyé à Claude (fenêtre active, sélection, capture)."""

from dataclasses import dataclass, field
from pathlib import Path

from custos.config import PrivacyConfig
from custos.context import clipboard, hyprland, privacy, screen
from custos.context.hyprland import ActiveWindow

MAX_SELECTION = 20_000  # caractères
MAX_OUTPUT = 8_000  # caractères : la fin de la sortie d'une commande, là où est l'erreur

DEFAULT_QUESTION = "Aide-moi avec ce texte : explique-le, ou corrige-le si c'est moi qui l'ai écrit."
FAILURE_QUESTION = "Pourquoi cette commande a échoué, et comment la corriger ?"


@dataclass
class Context:
    window: ActiveWindow | None = None
    selection: str = ""
    screenshot: Path | None = None
    blocked: bool = False  # fenêtre active dans la liste noire : rien n'a été lu
    failure: dict | None = None  # commande shell en échec (message du hook)
    attached: bool = False  # `window` est la fenêtre où la mascotte est accrochée
    image_kind: str = "window"  # d'où vient `screenshot` : window, clipboard ou screenshot
    # Images jointes par toi (collées, glissées), pour la prochaine question.
    attachments: list[Path] = field(default_factory=list)

    def images(self, first: bool) -> list[Path]:
        """Images à envoyer : la capture avec la première question, puis les pièces jointes."""
        return ([self.screenshot] if first and self.screenshot else []) + self.attachments

    def cleanup(self) -> None:
        if self.screenshot:
            self.screenshot.unlink(missing_ok=True)
            self.screenshot = None
        for path in self.attachments:
            path.unlink(missing_ok=True)
        self.attachments = []


IMAGE_LABELS = {
    "window": ("capture jointe", "Une capture de cette fenêtre est jointe."),
    "clipboard": ("image copiée", "Une image que je viens de copier est jointe."),
    "screenshot": ("capture d'écran", "Une capture d'écran (zone que j'ai choisie) est jointe."),
    "dropped": ("image déposée", "Une image que je viens de te donner est jointe."),
}
ATTACHMENT_NOTE = "Je joins aussi {count} image(s) à cette question."



def collect(
    config: PrivacyConfig,
    selection: bool = False,
    screenshot: bool = False,
    failure: dict | None = None,
    image: Path | None = None,
    image_kind: str = "window",
    window: ActiveWindow | None = None,
    attached: bool = False,
) -> Context:
    """Lit le contexte.

    `image` : image déjà prête (copie temporaire), jointe à la place d'une capture.
    `window` : fenêtre à lire (celle où la mascotte est accrochée) au lieu de la fenêtre active.
    """
    window = window or hyprland.active_window()
    if window and privacy.is_blocked(window.window_class, window.title, config):
        return Context(window=window, blocked=True)

    context = Context(window=window, failure=failure, attached=attached)
    if selection:
        text = clipboard.selection()
        if len(text) > MAX_SELECTION:
            text = text[:MAX_SELECTION] + "\n[… tronqué]"
        context.selection = privacy.redact_secrets(text)
    if image:
        context.screenshot, context.image_kind = image, image_kind
    elif screenshot and window:
        context.screenshot = screen.capture(window, config)
    return context


def app_name(window_class: str) -> str:
    """Nom lisible d'une appli à partir de sa classe Hyprland.

    « com.anthropic.Claude » → « Claude », « brave-browser » → « Brave browser ».
    """
    name = window_class.rsplit(".", 1)[-1].replace("-", " ").replace("_", " ").strip()
    return name[:1].upper() + name[1:]


def describe(context: Context) -> str:
    """Ce que Custos a lu, en haut de la carte ; vide quand ce n'est que la fenêtre active
    (sans capture, son nom n'apprend rien)."""
    app = app_name(context.window.window_class) if context.window else ""
    parts = []
    if context.failure:
        command = _short(privacy.redact_secrets(context.failure.get("command", "")))
        parts.append(f"commande en échec : {command}")
        if context.failure.get("output"):
            parts.append("sortie jointe")
    elif context.attached and app:
        parts.append(f"📌 {app}")
    if context.selection:
        parts.append(f"sélection : {len(context.selection)} car.")
    if context.screenshot:
        label = IMAGE_LABELS[context.image_kind][0]
        if context.image_kind == "window" and app and not context.attached:
            label = f"capture de {app}"
        parts.append(label)
    return " · ".join(parts)


def _short(text: str, limit: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def hint(context: Context) -> str:
    """Texte du champ de saisie vide : ce que fera Entrée sans rien écrire."""
    if context.failure:
        return "Entrée : pourquoi ça a échoué ?"
    if context.selection:
        return "Entrée : aide sur la sélection"
    if context.screenshot or context.attachments:
        return "Entrée : que vois-tu ?"
    return "Demande à Custos…"


def default_question(context: Context) -> str | None:
    """Question utilisée quand on valide la bulle sans rien écrire."""
    if context.failure:
        return FAILURE_QUESTION
    if context.selection:
        return DEFAULT_QUESTION
    if context.screenshot or context.attachments:
        return "Que vois-tu ? Aide-moi."
    return None


def build_prompt(question: str | None, context: Context) -> str:
    parts = []
    if context.window and not context.blocked:
        title = privacy.redact_secrets(context.window.title)
        parts.append(f"Fenêtre active : {context.window.window_class} — « {title} »")
    if context.failure:
        command = privacy.redact_secrets(context.failure.get("command", ""))
        cwd = context.failure.get("cwd") or "?"
        parts.append(
            f"Cette commande vient d'échouer (code de sortie {context.failure.get('exit_code')}, "
            f"dossier {cwd}) :\n```\n{command}\n```"
        )
        # Masquage avant de couper : un secret à cheval sur la coupure serait raté.
        output = privacy.redact_secrets(context.failure.get("output") or "")
        if output:
            if len(output) > MAX_OUTPUT:
                output = "[… début tronqué]\n" + output[-MAX_OUTPUT:]
            parts.append(f"Sa sortie :\n```\n{output}\n```")
    if context.screenshot:
        parts.append(IMAGE_LABELS[context.image_kind][1])
    if context.attachments:
        parts.append(ATTACHMENT_NOTE.format(count=len(context.attachments)))
    if context.selection:
        parts.append(f"Texte sélectionné :\n```\n{context.selection}\n```")

    question = question or default_question(context) or "Aide-moi."
    parts.append(f"Question : {question}")
    return "\n\n".join(parts)
