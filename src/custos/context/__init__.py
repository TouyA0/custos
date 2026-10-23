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


def build_prompt(question: str | None, context: Context) -> str:
    parts = []
    if context.window and not context.blocked:
        title = privacy.redact_secrets(context.window.title)
        parts.append(f"Fenêtre active : {context.window.window_class} — « {title} »")
    if context.screenshot:
        parts.append("Une capture de cette fenêtre est jointe.")
    if context.selection:
        parts.append(f"Texte sélectionné :\n```\n{context.selection}\n```")

    if not question:
        question = DEFAULT_QUESTION if context.selection else "Que vois-tu ? Aide-moi."
    parts.append(f"Question : {question}")
    return "\n\n".join(parts)
