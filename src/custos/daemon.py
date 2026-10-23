"""Daemon : affiche la mascotte, reçoit les messages et orchestre les appels à Claude."""

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Protocol

from custos import claude, context, ipc
from custos.config import Config, load
from custos.states import MascotState

log = logging.getLogger(__name__)

LAYER_SHELL_LIB = Path("/usr/lib/libgtk4-layer-shell.so")


class UI(Protocol):
    def set_state(self, state: MascotState) -> None: ...
    def show_answer(self, text: str) -> None: ...
    def show_error(self, text: str) -> None: ...


class Daemon:
    def __init__(self, config: Config, ui: UI) -> None:
        self.config = config
        self.ui = ui
        self.last_failure: dict | None = None  # dernière commande shell en échec
        self._busy = threading.Lock()  # une seule question à la fois

    def handle(self, message: dict) -> None:
        match message.get("type"):
            case "ask":
                self.ask(message)
            case "shell-event":
                self.shell_event(message)
            case other:
                log.warning("type de message inconnu : %s", other)

    def ask(self, message: dict) -> None:
        if not self._busy.acquire(blocking=False):
            self.ui.show_error("Je réfléchis déjà à ta question précédente.")
            return
        ctx = None
        try:
            ctx = context.collect(
                self.config.privacy,
                selection=bool(message.get("selection")),
                screenshot=bool(message.get("screen")),
            )
            if ctx.blocked:
                self.ui.show_error("Cette fenêtre est protégée : je n'ai rien lu.")
                return

            question = message.get("question")
            if not question and not ctx.selection and not ctx.screenshot:
                # La bulle de saisie viendra avec l'interface GTK.
                self.ui.show_error("Pose ta question : custos ask \"…\"")
                return

            self.ui.set_state(MascotState.THINKING)
            prompt = context.build_prompt(question, ctx)
            images = [ctx.screenshot] if ctx.screenshot else None
            log.info(
                "question (sélection : %d car., capture : %s)",
                len(ctx.selection),
                "oui" if images else "non",
            )
            started = time.monotonic()
            answer = "".join(claude.ask(prompt, self.config.claude, images=images))
            log.info("réponse en %.1f s (%d car.)", time.monotonic() - started, len(answer))
            self.ui.show_answer(answer.strip())
        except claude.ClaudeError as exc:
            log.warning("erreur Claude : %s", exc)
            self.ui.set_state(MascotState.IDLE)
            self.ui.show_error(str(exc))
        finally:
            if ctx:
                ctx.cleanup()
            self._busy.release()

    def shell_event(self, message: dict) -> None:
        # Réaction locale uniquement : Claude n'est pas appelé ici.
        if message.get("exit_code", 0) == 0 or not self.config.shell.react_on_failure:
            return
        self.last_failure = message
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

            handler.append(Daemon(config, OverlayUI(app)).handle)
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
