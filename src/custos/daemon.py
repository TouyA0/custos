"""Daemon : affiche la mascotte, reçoit les messages et orchestre les appels à Claude."""

import logging
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from custos import claude, context, ipc, watch
from custos.config import Config, load
from custos.context import Context, clipboard, hyprland, privacy
from custos.states import MascotState
from custos.supervisor import EXIT_ALREADY_RUNNING, setup_logging

log = logging.getLogger(__name__)

MIN_SELECTION = 3  # caractères : en dessous, ce n'est sans doute pas une vraie sélection
# Événements Hyprland après lesquels le plein écran a pu changer.
FULLSCREEN_EVENTS = {"fullscreen", "activewindowv2", "workspacev2", "closewindow", "focusedmonv2"}
RESUME_WINDOW = 600.0  # secondes : rouvrir la carte dans ce délai reprend la conversation
RECENT_FAILURE = 30.0  # secondes : même durée que l'inquiétude de la mascotte
IGNORED_EXIT_CODES = {130, 148}  # Ctrl+C et Ctrl+Z : interrompre n'est pas une erreur
# Sortie de la commande en échec : terminaux pris en charge, et raccourci que leur config
# associe à `custos shell-output` (voir integrations/terminal/foot.ini).
OUTPUT_TERMINALS = {"foot"}
OUTPUT_SHORTCUT = ("CTRL SHIFT ALT", "F12")
OUTPUT_DELAY = 0.3  # secondes : le temps que le terminal marque la fin de la sortie
OUTPUT_TIMEOUT = 5.0  # secondes : au-delà, une sortie reçue n'est plus celle demandée


class UI(Protocol):
    def set_state(self, state: MascotState) -> None: ...
    def open_prompt(self, summary: str, hint: str) -> None: ...
    def start_answer(self, question: str) -> None: ...
    def append_answer(self, text: str) -> None: ...
    def end_answer(self, answer: str) -> None: ...
    def show_error(self, text: str) -> None: ...
    def show_notice(self, kind: str | None) -> None: ...
    def reopen(self, summary: str, question: str, answer: str) -> None: ...
    def set_hidden(self, hidden: bool) -> None: ...
    def attach(self, window: hyprland.ActiveWindow) -> None: ...
    def detach(self) -> None: ...
    def toggle_stowed(self) -> None: ...


@dataclass
class Notice:
    """Quelque chose que Custos a remarqué (sélection, image) et propose d'analyser."""

    kind: str  # voir watch.SELECTION, CLIPBOARD_IMAGE, SCREENSHOT
    at: float
    path: str | None = None  # fichier de la capture caelestia


@dataclass
class Session:
    """Ce que Custos a lu au moment du raccourci, et la conversation qui a suivi."""

    context: Context
    claude_session: str | None = None  # session Claude Code, reprise pour les questions de suivi
    summary: str = ""  # ce que Custos a lu, affiché en haut de la carte
    last_question: str = ""
    last_answer: str = ""


class Daemon:
    def __init__(self, config: Config, ui: UI) -> None:
        self.config = config
        self.ui = ui
        self.session: Session | None = None
        self.last_failure: dict | None = None  # dernière commande shell en échec
        self._failure_at = 0.0
        self._output_requested_at: float | None = None
        self.notice: Notice | None = None
        self.hidden = False  # mascotte masquée par une fenêtre en plein écran
        self.attached: str | None = None  # adresse de la fenêtre où la mascotte est accrochée
        self._last_selection_hash: int | None = None
        self._busy = threading.Lock()  # une seule question à Claude à la fois
        self._cancel = threading.Event()  # levé quand on ferme la carte : la réponse s'arrête
        self._closed_at = 0.0

    def handle(self, message: dict) -> None:
        match message.get("type"):
            case "ask":
                self.ask(message)
            case "shell-event":
                self.shell_event(message)
            case "shell-output":
                self.shell_output(message)
            case "attach":
                self.attach_active()
            case "detach":
                self.ui.detach()
            case "toggle":
                self.ui.toggle_stowed()
            case "open":
                self.open_full()
            case other:
                log.warning("type de message inconnu : %s", other)

    def ask(self, message: dict) -> None:
        """Raccourci ou `custos ask` : lit le contexte, puis répond ou ouvre la bulle.

        Sans rien de nouveau à montrer (ni question, sélection, image, erreur ou fenêtre
        accrochée), rouvrir la carte peu après reprend la conversation précédente.
        """
        if self._busy.locked():
            self.ui.show_error("Je réfléchis déjà à ta question précédente.")
            return
        if self._can_resume(message):
            session = self.session
            log.info("conversation reprise")
            self.ui.reopen(session.summary, session.last_question, session.last_answer)
            return
        image = message.get("image")
        target = self._attached_window(message)
        ctx = context.collect(
            self.config.privacy,
            selection=bool(message.get("selection")),
            screenshot=bool(message.get("screen")) or target is not None,
            failure=message.get("failure"),
            image=Path(image) if image else None,
            image_kind=message.get("image_kind", "window"),
            window=target,
            attached=target is not None,
        )
        if ctx.blocked:
            self.ui.show_error("Cette fenêtre est protégée : je n'ai rien lu.")
            return

        self.close_session()
        self.session = Session(ctx, summary=context.describe(ctx))
        if message.get("question"):
            self.answer(message["question"])
        else:
            self.ui.open_prompt(self.session.summary, context.hint(ctx))

    def _can_resume(self, message: dict) -> bool:
        fresh = ("question", "selection", "screen", "image", "failure", "new")
        session = self.session
        return (
            not any(message.get(key) for key in fresh)
            and not self.attached  # accrochée : une nouvelle capture de la fenêtre à chaque fois
            and session is not None
            and bool(session.claude_session and session.last_answer)
            and time.monotonic() - self._closed_at < RESUME_WINDOW
        )

    def new_conversation(self) -> None:
        """Bouton ＋ : oublie la conversation en cours et en commence une nouvelle."""
        self.close_session()
        self.ask({"type": "ask", "new": True})

    def close_bubble(self) -> None:
        """Carte fermée : on arrête la réponse en cours et on garde la conversation un moment."""
        self._cancel.set()
        self._closed_at = time.monotonic()
        session = self.session
        if session and not session.claude_session:
            self.close_session()  # rien à reprendre : on supprime aussi la capture

    def open_from_mascot(self) -> None:
        """Clic sur la mascotte : propose d'abord ce qu'elle vient de remarquer.

        Par ordre de priorité : une commande qui vient d'échouer, une sélection ou une
        image remarquée, sinon une bulle vide.
        """
        now = time.monotonic()
        failure = self.last_failure if now - self._failure_at < RECENT_FAILURE else None
        notice = self.notice if self.notice and now - self.notice.at < self.config.notice.duration else None
        self.last_failure = None
        self._clear_notice()

        if failure:
            # La fenêtre active est sans doute le terminal : sa capture montre le message d'erreur.
            self.ask({"type": "ask", "screen": True, "failure": failure})
        elif notice and notice.kind == watch.SELECTION:
            self.ask({"type": "ask", "selection": True})
        elif notice:
            image = self._copy_noticed_image(notice)
            if image is None:
                self.ui.show_error("Je n'arrive plus à lire cette image.")
                return
            kind = "screenshot" if notice.kind == watch.SCREENSHOT else "clipboard"
            self.ask({"type": "ask", "image": str(image), "image_kind": kind})
        else:
            self.ask({"type": "ask"})

    def on_watch_event(self, kind: str, path: str | None = None) -> None:
        """Changement de sélection, image copiée ou capture : allume le badge (sans appeler Claude)."""
        if self.session or self._busy.locked():
            return  # bulle ouverte : on ne dérange pas (et sa propre sélection ne compte pas)
        if kind != watch.SCREENSHOT:
            window = hyprland.active_window()
            if window and privacy.is_blocked(window.window_class, window.title, self.config.privacy):
                return
        if kind == watch.SELECTION:
            text = clipboard.selection().strip()
            if len(text) < MIN_SELECTION or hash(text) == self._last_selection_hash:
                return
            self._last_selection_hash = hash(text)
        self.notice = Notice(kind, time.monotonic(), path)
        self.ui.show_notice(kind)

    def _attached_window(self, message: dict) -> hyprland.ActiveWindow | None:
        """Fenêtre accrochée à lire, pour une question « simple » (sans sélection, image ni erreur)."""
        if not self.attached or any(message.get(k) for k in ("selection", "image", "failure")):
            return None
        return hyprland.find_window(self.attached)

    def attach_active(self) -> None:
        """`custos attach` : accroche la mascotte à la fenêtre active."""
        window = hyprland.active_window()
        if window is None or not window.address:
            self.ui.show_error("Aucune fenêtre active à laquelle m'accrocher.")
            return
        self.ui.attach(window)  # l'overlay rappelle set_attached une fois accrochée

    def set_attached(self, window: hyprland.ActiveWindow | None) -> None:
        """Appelé par l'overlay quand la mascotte s'accroche ou se détache."""
        if window and privacy.is_blocked(window.window_class, window.title, self.config.privacy):
            self.ui.detach()
            self.ui.show_error("Cette fenêtre est protégée : je ne m'y accroche pas.")
            return
        self.attached = window.address if window else None
        log.info("accrochée à une fenêtre" if window else "détachée")

    def on_hyprland_event(self, name: str, _data: str = "") -> None:
        """Masque la mascotte tant que la fenêtre active est en plein écran (vidéo, jeu…)."""
        if name not in FULLSCREEN_EVENTS or not self.config.mascot.hide_in_fullscreen:
            return
        hidden = hyprland.active_is_fullscreen()
        if hidden != self.hidden:
            self.hidden = hidden
            log.info("plein écran : mascotte %s", "masquée" if hidden else "réaffichée")
            self.ui.set_hidden(hidden)

    def _clear_notice(self) -> None:
        if self.notice:
            self.notice = None
            self.ui.show_notice(None)

    def _copy_noticed_image(self, notice: Notice) -> Path | None:
        """Copie temporaire de l'image : la session la supprime après usage, pas l'original."""
        if notice.kind == watch.CLIPBOARD_IMAGE:
            return clipboard.image_to_file()
        if notice.path and Path(notice.path).exists():
            fd, name = tempfile.mkstemp(
                prefix="custos-", suffix=".png", dir=os.environ.get("XDG_RUNTIME_DIR")
            )
            os.close(fd)
            shutil.copyfile(notice.path, name)
            return Path(name)
        return None

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
        self._cancel.clear()
        try:
            # Première question : avec le contexte (fenêtre, sélection, capture). Les suivantes
            # reprennent la session Claude, qui a déjà tout ça : juste la question.
            resume = session.claude_session
            prompt = question if resume else context.build_prompt(question, ctx)
            images = [ctx.screenshot] if ctx.screenshot and not resume else None
            log.info(
                "question (sélection : %d car., capture : %s, suivi : %s)",
                len(ctx.selection),
                "oui" if images else "non",
                "oui" if resume else "non",
            )
            self.ui.set_state(MascotState.THINKING)
            self.ui.start_answer(question)
            started = time.monotonic()
            chunks = []

            def remember(session_id: str) -> None:
                session.claude_session = session_id

            for chunk in claude.ask(
                prompt,
                self.config.claude,
                images=images,
                resume=resume,
                on_session=remember,
                cancel=self._cancel,
            ):
                chunks.append(chunk)
                self.ui.append_answer(chunk)
            ctx.cleanup()  # la capture n'est envoyée qu'avec la première question
            if self._cancel.is_set():
                log.info("réponse interrompue (carte fermée)")
                self.ui.set_state(MascotState.IDLE)
                return
            answer = "".join(chunks).strip()
            log.info("réponse en %.1f s (%d car.)", time.monotonic() - started, len(answer))

            session.last_question, session.last_answer = question, answer
            self.ui.end_answer(answer)
            self.ui.set_state(MascotState.TALKING)
        except claude.ClaudeError as exc:
            log.warning("erreur Claude : %s", exc)
            self.ui.set_state(MascotState.IDLE)
            self.ui.show_error(str(exc))
        finally:
            self._busy.release()

    def open_full(self) -> None:
        """Ouvre la conversation en cours en grand : Claude Code dans un terminal."""
        session_id = self.session.claude_session if self.session else None
        if not session_id:
            self.ui.show_error("Pose d'abord une question.")
            return
        command = self.config.claude.open_command.format(
            session=session_id,
            cwd=shlex.quote(str(claude.conversations_dir())),
            model=self.config.claude.model,
        )
        try:
            subprocess.Popen(
                shlex.split(command),
                cwd=claude.conversations_dir(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            self.ui.show_error(f"Impossible d'ouvrir le terminal : {exc}")
            return
        log.info("conversation ouverte en grand")

    def shell_event(self, message: dict) -> None:
        # Réaction locale uniquement : Claude n'est pas appelé ici.
        exit_code = message.get("exit_code", 0)
        if exit_code == 0 or exit_code in IGNORED_EXIT_CODES or not self.config.shell.react_on_failure:
            return
        self.last_failure = message
        self._failure_at = time.monotonic()
        log.info("commande en échec (code %s)", message.get("exit_code"))
        self.ui.set_state(MascotState.WORRIED)
        if self.config.shell.capture_output:
            # La commande vient de finir : la fenêtre active est le terminal où elle a tourné.
            window = hyprland.active_window()
            if window and window.window_class in OUTPUT_TERMINALS and not privacy.is_blocked(
                window.window_class, window.title, self.config.privacy
            ):
                _later(OUTPUT_DELAY, self._request_output, window.address)

    def _request_output(self, address: str) -> None:
        """Demande au terminal la sortie de la dernière commande (réponse : `shell-output`).

        Rien n'est envoyé à Claude ici : la sortie n'est jointe que si tu demandes de l'aide.
        """
        self._output_requested_at = time.monotonic()
        if not hyprland.send_shortcut(*OUTPUT_SHORTCUT, address):
            log.warning("impossible de demander la sortie de la commande au terminal")

    def shell_output(self, message: dict) -> None:
        """Sortie de la commande en échec, envoyée par le terminal via `custos shell-output`."""
        requested = self._output_requested_at
        self._output_requested_at = None
        if requested is None or time.monotonic() - requested > OUTPUT_TIMEOUT:
            return  # pas demandée par Custos : on l'ignore
        if self.last_failure is None:
            return
        output = (message.get("output") or "").rstrip()
        self.last_failure["output"] = output
        log.info("sortie de la commande reçue (%d car.)", len(output))


def _later(delay: float, action, *args) -> None:
    timer = threading.Timer(delay, action, args)
    timer.daemon = True
    timer.start()


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
    """Le vrai daemon, lancé par le superviseur (voir supervisor.py)."""
    # Ses lignes de log partent sur stderr, que le superviseur redirige vers le fichier de log.
    setup_logging("daemon", to_file=False)
    config = load()

    handler: list = []  # rempli une fois le daemon créé
    try:
        server = ipc.Server(lambda message: handler[0](message))
    except ipc.DaemonAlreadyRunning:
        log.error("un daemon Custos tourne déjà")
        return EXIT_ALREADY_RUNNING
    log.info("à l'écoute sur %s", server.path)

    app = _gtk_app(config)
    watcher = None
    stop_events = threading.Event()
    try:
        if app is None:
            from custos.ui.notify import NotifyUI

            daemon = Daemon(config, NotifyUI())
            handler.append(daemon.handle)
            _serve_headless(server)
        else:
            from custos.ui.overlay import OverlayUI

            ui = OverlayUI(app, config.notice.duration)
            daemon = Daemon(config, ui)
            ui.connect(daemon)
            handler.append(daemon.handle)
            watcher = _start_watcher(config, daemon)
            threading.Thread(
                target=hyprland.listen, args=(daemon.on_hyprland_event, stop_events), daemon=True
            ).start()
            daemon.on_hyprland_event("fullscreen")  # déjà en plein écran au démarrage ?
            _serve_with_gtk(server, app)
    finally:
        stop_events.set()
        if watcher:
            watcher.stop()
        server.server_close()
        log.info("arrêté")
    return 0


def _start_watcher(config: Config, daemon: Daemon) -> watch.Watcher:
    # Seulement avec la mascotte : sans elle, pas de badge à allumer ni de clic possible.
    notice = config.notice
    watcher = watch.Watcher(
        daemon.on_watch_event,
        selection=notice.selection,
        images=notice.images,
        screenshot_glob=notice.screenshot_glob if notice.screenshots else "",
    )
    watcher.start()
    return watcher


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
