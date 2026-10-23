"""Affichage provisoire par notifications (`notify-send`), en attendant l'overlay GTK."""

import html
import logging
import subprocess

from custos.states import MascotState

log = logging.getLogger(__name__)

STATE_MESSAGES = {
    MascotState.THINKING: "Je réfléchis…",
    MascotState.WORRIED: "Une commande vient d'échouer.",
}


def notify(title: str, body: str, replace_id: int | None = None) -> int | None:
    """Affiche une notification et renvoie son identifiant (pour la remplacer ensuite)."""
    cmd = ["notify-send", "--app-name", "Custos", "--print-id"]
    if replace_id is not None:
        cmd += ["--replace-id", str(replace_id)]
    # Les serveurs de notification interprètent un peu de HTML : on échappe le texte.
    cmd += [title, html.escape(body, quote=False)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        log.warning("notify-send indisponible")
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


class NotifyUI:
    """Même rôle que la future mascotte : changer d'état et afficher une réponse."""

    def __init__(self) -> None:
        self.state = MascotState.IDLE
        self._notification: int | None = None

    def set_state(self, state: MascotState) -> None:
        self.state = state
        message = STATE_MESSAGES.get(state)
        if message:
            self._notification = notify("Custos", message, self._notification)

    def show_answer(self, text: str) -> None:
        self.state = MascotState.TALKING
        self._notification = notify("Custos", text, self._notification)

    def show_error(self, text: str) -> None:
        self.state = MascotState.IDLE
        self._notification = notify("Custos — oups", text, self._notification)
