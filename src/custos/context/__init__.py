"""Collecte du contexte envoyé à Claude (fenêtre active, sélection, capture)."""

from dataclasses import dataclass
from pathlib import Path

from custos.config import PrivacyConfig
from custos.context import clipboard, hyprland, privacy, screen
from custos.context.hyprland import ActiveWindow

MAX_SELECTION = 20_000  # caractères

DEFAULT_QUESTION = "Aide-moi avec ce texte : explique-le, ou corrige-le si c'est moi qui l'ai écrit."


@dataclass
class Context:
    window: ActiveWindow | None = None
    selection: str = ""
    screenshot: Path | None = None
    blocked: bool = False  # fenêtre active dans la liste noire : rien n'a été lu

    def cleanup(self) -> None:
        if self.screenshot:
            self.screenshot.unlink(missing_ok=True)
            self.screenshot = None


def collect(config: PrivacyConfig, selection: bool = False, screenshot: bool = False) -> Context:
    window = hyprland.active_window()
    if window and privacy.is_blocked(window.window_class, config.blocked_classes):
        return Context(window=window, blocked=True)

    context = Context(window=window)
    if selection:
        text = clipboard.selection()
        if len(text) > MAX_SELECTION:
            text = text[:MAX_SELECTION] + "\n[… tronqué]"
        context.selection = privacy.redact_secrets(text)
    if screenshot and window:
        context.screenshot = screen.capture(window)
    return context


def describe(context: Context) -> str:
    """Résumé court de ce que Custos a lu, affiché en haut de la bulle."""
    parts = []
    if context.window:
        parts.append(context.window.window_class)
    if context.selection:
        parts.append(f"sélection : {len(context.selection)} car.")
    if context.screenshot:
        parts.append("capture jointe")
    return " · ".join(parts)


def default_question(context: Context) -> str | None:
    """Question utilisée quand on valide la bulle sans rien écrire."""
    if context.selection:
        return DEFAULT_QUESTION
    if context.screenshot:
        return "Que vois-tu ? Aide-moi."
    return None


def build_prompt(
    question: str | None,
    context: Context,
    history: list[tuple[str, str]] | None = None,
) -> str:
    parts = []
    if history:
        exchanges = "\n\n".join(f"Moi : {q}\n\nToi : {a}" for q, a in history)
        parts.append(f"Notre conversation jusqu'ici :\n\n{exchanges}")
    if context.window and not context.blocked:
        title = privacy.redact_secrets(context.window.title)
        parts.append(f"Fenêtre active : {context.window.window_class} — « {title} »")
    if context.screenshot:
        parts.append("Une capture de cette fenêtre est jointe.")
    if context.selection:
        parts.append(f"Texte sélectionné :\n```\n{context.selection}\n```")

    question = question or default_question(context) or "Aide-moi."
    parts.append(f"Question : {question}")
    return "\n\n".join(parts)
