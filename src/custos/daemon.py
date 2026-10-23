"""Daemon : affiche la mascotte, reçoit les messages et orchestre les appels à Claude."""

import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from custos import claude, context, ipc
from custos.config import Config, load
from custos.context import Context
from custos.states import MascotState

log = logging.getLogger(__name__)

LAYER_SHELL_LIB = Path("/usr/lib/libgtk4-layer-shell.so")

RECENT_FAILURE = 30.0  # secondes : même durée que l'inquiétude de la mascotte
IGNORED_EXIT_CODES = {130, 148}  # Ctrl+C et Ctrl+Z : interrompre n'est pas une erreur


class UI(Protocol):
    def set_state(self, state: MascotState) -> None: ...
    def open_prompt(self, summary: str, hint: str) -> None: ...
    def start_answer(self, question: str) -> None: ...
    def append_answer(self, text: str) -> None: ...
    def end_answer(self, answer: str) -> None: ...
    def show_error(self, text: str) -> None: ...


@dataclass
class Session:
    """Ce que Custos a lu au moment du raccourci, et la conversation qui a suivi."""

    context: Context
    history: list[tuple[str, str]] = field(default_factory=list)


class Daemon:
    def __init__(self, config: Config, ui: UI) -> None:
        self.config = config
        self.ui = ui
        self.session: Session | None = None
        self.last_failure: dict | None = None  # dernière commande shell en échec
        self._failure_at = 0.0
        self._busy = threading.Lock()  # une seule question à Claude à la fois

    def handle(self, message: dict) -> None:
        match message.get("type"):
            case "ask":
                self.ask(message)
            case "shell-event":
                self.shell_event(message)
            case other:
                log.warning("type de message inconnu : %s", other)

    def ask(self, message: dict) -> None:
        """Raccourci ou `custos ask` : lit le contexte, puis répond ou ouvre la bulle."""
        if self._busy.locked():
            self.ui.show_error("Je réfléchis déjà à ta question précédente.")
            return
        ctx = context.collect(
            self.config.privacy,
            selection=bool(message.get("selection")),
            screenshot=bool(message.get("screen")),
            failure=message.get("failure"),
        )
        if ctx.blocked:
            self.ui.show_error("Cette fenêtre est protégée : je n'ai rien lu.")
            return

        self.close_session()
        self.session = Session(ctx)
        if message.get("question"):
            self.answer(message["question"])
        else:
            self.ui.open_prompt(context.describe(ctx), context.hint(ctx))

    def open_from_mascot(self) -> None:
        """Clic sur la mascotte : si une commande vient d'échouer, la bulle propose de l'expliquer."""
        failure = None
        if self.last_failure and time.monotonic() - self._failure_at < RECENT_FAILURE:
            failure = self.last_failure
        self.last_failure = None
        # La fenêtre active est sans doute le terminal : sa capture montre le message d'erreur.
        self.ask({"type": "ask", "screen": failure is not None, "failure": failure})

    def submit(self, question: str) -> None:
        """Question tapée dans la bulle (thread GTK) : on répond dans un autre thread."""
        threading.Thread(target=self.answer, args=(question,), daemon=True).start()

    def close_session(self) -> None:
        if self.session:
            self.session.context.cleanup()
            self.session = None

    def answer(self, question: str) -> None:
        session = self.session or Session(Context())
        self.session = session
        ctx = session.context
        question = question.strip() or context.default_question(ctx) or ""
        if not question:
            self.ui.show_error("Écris ta question.")
            return
        if not self._busy.acquire(blocking=False):
            self.ui.show_error("Je réfléchis déjà à ta question précédente.")
            return
        try:
            prompt = context.build_prompt(question, ctx, session.history)
            images = [ctx.screenshot] if ctx.screenshot else None
            log.info(
                "question (sélection : %d car., capture : %s, échanges précédents : %d)",
                len(ctx.selection),
                "oui" if images else "non",
                len(session.history),
            )
            self.ui.set_state(MascotState.THINKING)
            self.ui.start_answer(question)
            started = time.monotonic()
            chunks = []
            for chunk in claude.ask(prompt, self.config.claude, images=images):
                chunks.append(chunk)
                self.ui.append_answer(chunk)
            answer = "".join(chunks).strip()
            log.info("réponse en %.1f s (%d car.)", time.monotonic() - started, len(answer))

            session.history.append((question, answer))
            ctx.cleanup()  # la capture n'est envoyée qu'avec la première question
            self.ui.end_answer(answer)
            self.ui.set_state(MascotState.TALKING)
        except claude.ClaudeError as exc:
            log.warning("erreur Claude : %s", exc)
            self.ui.set_state(MascotState.IDLE)
            self.ui.show_error(str(exc))
        finally:
            self._busy.release()

    def shell_event(self, message: dict) -> None:
        # Réaction locale uniquement : Claude n'est pas appelé ici.
        exit_code = message.get("exit_code", 0)
        if exit_code == 0 or exit_code in IGNORED_EXIT_CODES or not self.config.shell.react_on_failure:
            return
        self.last_failure = message
        self._failure_at = time.monotonic()
        log.info("commande en échec (code %s)", message.get("exit_code"))
        self.ui.set_state(MascotState.WORRIED)


def _preload_layer_shell() -> None:
    """gtk4-layer-shell doit être chargé avant libwayland : on se relance avec LD_PRELOAD."""
    preload = os.environ.get("LD_PRELOAD", "")
    if "libgtk4-layer-shell" in preload or not LAYER_SHELL_LIB.exists():
        return
    env = dict(os.environ, LD_PRELOAD=f"{LAYER_SHELL_LIB} {preload}".strip())
    os.execve(sys.executable, [sys.executable, "-m", "custos", "daemon"], env)


def _gtk_app(config: Config):
    """Application GTK de la mascotte, ou None si l'overlay est impossible (pas de layer-shell…)."""
    try:
        from custos.ui import overlay
    except (ImportError, ValueError) as exc:
        log.warning("mascotte indisponible (%s) : réponses par notification", exc)
        return None
    if not overlay.is_supported():
        log.warning("le compositeur ne gère pas layer-shell : réponses par notification")
        return None
    return overlay.MascotApp(config.mascot)


def run() -> int:
    _preload_layer_shell()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s custos %(levelname)s %(message)s")
    config = load()

    handler: list = []  # rempli une fois le daemon créé
    try:
        server = ipc.Server(lambda message: handler[0](message))
    except ipc.DaemonAlreadyRunning:
        log.error("un daemon Custos tourne déjà")
        return 1
    log.info("à l'écoute sur %s", server.path)

    app = _gtk_app(config)
    try:
        if app is None:
            from custos.ui.notify import NotifyUI

            handler.append(Daemon(config, NotifyUI()).handle)
            _serve_headless(server)
        else:
            from custos.ui.overlay import OverlayUI

            ui = OverlayUI(app)
            daemon = Daemon(config, ui)
            ui.connect(daemon)
            handler.append(daemon.handle)
            _serve_with_gtk(server, app)
    finally:
        server.server_close()
        log.info("arrêté")
    return 0


def _serve_headless(server: ipc.Server) -> None:
    # SIGTERM (fin de session Hyprland) arrête proprement, comme Ctrl+C.
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown).start())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def _serve_with_gtk(server: ipc.Server, app) -> None:
    from gi.repository import GLib

    # GTK occupe le thread principal ; le serveur IPC tourne à côté.
    threading.Thread(target=server.serve_forever, daemon=True).start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: app.quit() or GLib.SOURCE_REMOVE)
    try:
        app.run([])
    finally:
        server.shutdown()
