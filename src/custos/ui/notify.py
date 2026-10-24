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
    """Repli sans overlay : même interface que la bulle (voir daemon.UI), par notifications."""

    def __init__(self) -> None:
        self.state = MascotState.IDLE
        self._notification: int | None = None

    def set_state(self, state: MascotState) -> None:
        self.state = state
        message = STATE_MESSAGES.get(state)
        if message:
            self._notification = notify("Custos", message, self._notification)

    def open_prompt(self, summary: str, hint: str) -> None:
        # Pas de bulle de saisie sans overlay.
        self.show_error('Pose ta question : custos ask "…"')

    def start_answer(self, question: str) -> None:
        pass

    def append_answer(self, text: str) -> None:
        pass

    def end_answer(self, answer: str) -> None:
        self._notification = notify("Custos", answer, self._notification)

    def show_error(self, text: str) -> None:
        self._notification = notify("Custos — oups", text, self._notification)

    def show_notice(self, kind: str | None) -> None:
        pass  # pas de mascotte, pas de badge

    def set_hidden(self, hidden: bool) -> None:
        pass

    def attach(self, window) -> None:
        self.show_error("Pas de mascotte à accrocher sans l'overlay.")

    def detach(self) -> None:
        pass

    def toggle_stowed(self) -> None:
        pass

    def monitors_changed(self) -> None:
        pass

    # Sans carte, personne pour valider une action sensible : Custos répond sans outils.
    interactive = False

    def set_status(self, text: str) -> None:
        pass

    def reset_answer(self) -> None:
        pass

    def ask_permission(self, description: str) -> None:
        pass

    def permission_done(self) -> None:
        pass

    def show_progress(self, question: str, status: str, answer: str, permission: str) -> None:
        pass

    def set_alert(self, alert: str | None, text: str) -> None:
        pass  # pas de barre sans overlay

    def set_attachments(self, count: int) -> None:
        pass  # pas de carte où coller une image

    def set_history(self, exchanges: list[tuple[str, str]]) -> None:
        pass  # pas de carte : seulement la dernière réponse

    def reopen(self, summary: str, question: str, answer: str) -> None:
        self.end_answer(answer)
