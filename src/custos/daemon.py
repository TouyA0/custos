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
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Protocol

from custos import claude, context, history, ipc, machine, quota, system, terminal, watch
from custos.config import Config, load, load_persona
from custos.context import Context, clipboard, hyprland, images, privacy
from custos.states import MascotState
from custos.supervisor import EXIT_ALREADY_RUNNING, setup_logging
from custos.ui import slash

log = logging.getLogger(__name__)

MIN_SELECTION = 3  # caractères : en dessous, ce n'est sans doute pas une vraie sélection
# Événements Hyprland après lesquels le plein écran a pu changer.
FULLSCREEN_EVENTS = {
    "fullscreen", "activewindowv2", "workspacev2", "closewindow", "focusedmonv2", "movewindowv2",
    "monitoraddedv2", "monitorremovedv2",
}
MONITOR_EVENTS = {"monitoraddedv2", "monitorremovedv2"}  # écran branché ou débranché
RESUME_WINDOW = 600.0  # secondes : rouvrir la carte dans ce délai reprend la conversation
RECENT_FAILURE = 30.0  # secondes : même durée que l'inquiétude de la mascotte
IGNORED_EXIT_CODES = {130, 148}  # Ctrl+C et Ctrl+Z : interrompre n'est pas une erreur
# Sortie de la commande en échec : terminaux pris en charge, et raccourci que leur config
# associe à `custos shell-output` (voir integrations/terminal/foot.ini).
OUTPUT_TERMINALS = {"foot"}
OUTPUT_SHORTCUT = ("CTRL SHIFT ALT", "F12")
OUTPUT_DELAY = 0.3  # secondes : le temps que le terminal marque la fin de la sortie
# Sessions Claude Code (hooks, voir integrations/claude-code) : badges sur la mascotte.
AGENT_WAITING = "agent-waiting"  # attend ta permission ou ta réponse : reste affiché
AGENT_DONE = "agent-done"  # a fini de répondre
WAITING_NOTIFICATIONS = {"permission_prompt", "elicitation_dialog"}
# Le travail de Custos lui-même, carte fermée : il attend ton accord, ou il a fini.
CUSTOS_WAITING = "custos-waiting"
ANSWER_READY = "answer-ready"
ALERT_WAITING, ALERT_DONE = "waiting", "done"  # pastille de l'icône de la barre (voir tray)
ALERT_PROBLEM = "problem"
# Un problème de la machine remarqué localement (service, disque, mise à jour, plantage, code).
SYSTEM_PROBLEM = "system-problem"
MAX_CODE_ERRORS = 15  # erreurs du fichier jointes à la question
OUTPUT_TIMEOUT = 5.0  # secondes : au-delà, une sortie reçue n'est plus celle demandée
MENU_HISTORY = 6  # conversations récentes dans le menu de l'icône
# Sessions Claude Code dans le menu de l'icône : (état en toutes lettres, (icône, couleur)).
AGENT_STATES = {
    "working": ("travaille", ("working", "#d9a33a")),
    "waiting": ("attend ton accord", ("attention", "#e8743b")),
    "done": ("a fini", ("check", "#4caf50")),
}


class UI(Protocol):
    def set_state(self, state: MascotState) -> None: ...
    def open_prompt(self, summary: str, hint: str) -> None: ...
    def start_answer(self, question: str) -> None: ...
    def append_answer(self, text: str) -> None: ...
    def end_answer(self, answer: str) -> None: ...
    def show_error(self, text: str) -> None: ...
    def show_notice(self, kind: str | None) -> None: ...
    def reopen(self, summary: str, question: str, answer: str) -> None: ...
    def set_history(self, exchanges: list[tuple[str, str]]) -> None: ...
    def set_attachments(self, count: int) -> None: ...
    def set_quota(self, quota: quota.Quota | None) -> None: ...
    def set_hidden(self, hidden: bool) -> None: ...
    def attach(self, window: hyprland.ActiveWindow) -> None: ...
    def detach(self) -> None: ...
    def toggle_stowed(self) -> None: ...
    def monitors_changed(self) -> None: ...
    def set_status(self, text: str) -> None: ...
    def reset_answer(self) -> None: ...
    def ask_permission(self, description: str) -> None: ...
    def permission_done(self) -> None: ...
    def show_progress(self, question: str, status: str, answer: str, permission: str) -> None: ...
    def set_alert(self, alert: str | None, text: str) -> None: ...


@dataclass
class Notice:
    """Quelque chose que Custos a remarqué (sélection, image) et propose d'analyser."""

    kind: str  # voir watch.SELECTION, CLIPBOARD_IMAGE, SCREENSHOT
    at: float
    path: str | None = None  # fichier de la capture caelestia


@dataclass
class Agent:
    """Une session Claude Code suivie grâce à ses hooks."""

    session: str
    project: str  # nom du dossier où elle tourne
    state: str  # working, waiting ou done
    pids: list[int]  # processus parents du hook : Claude Code, le shell, le terminal…
    at: float

    def alive(self) -> bool:
        """Claude Code tourne encore (sans hook SessionEnd s'il a été tué)."""
        # pids[0] est le shell éphémère qui lance le hook ; pids[1], Claude Code.
        pid = self.pids[1] if len(self.pids) > 1 else (self.pids[0] if self.pids else 0)
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, ValueError):
            return False
        except PermissionError:
            pass
        return pid > 0


@dataclass
class Work:
    """La question à laquelle Claude travaille en ce moment."""

    question: str
    status: str = ""  # étape en cours (« $ journalctl -b -1 »)
    chunks: list[str] = field(default_factory=list)  # réponse reçue depuis la dernière étape
    permission: claude.Permission | None = None  # action qui attend ton accord
    choice: str = claude.DENY
    answered: threading.Event = field(default_factory=threading.Event)


@dataclass
class Session:
    """Ce que Custos a lu au moment du raccourci, et la conversation qui a suivi."""

    context: Context
    claude_session: str | None = None  # session Claude Code, reprise pour les questions de suivi
    summary: str = ""  # ce que Custos a lu, affiché en haut de la carte
    last_question: str = ""
    last_answer: str = ""
    # (question, modèle, ce qu'affiche la carte) pour `/r`, même ratée
    last_asked: tuple[str, str | None, str] | None = None
    # Questions et réponses déjà finies, dans l'ordre : la carte montre les précédentes au-dessus.
    exchanges: list[tuple[str, str]] = field(default_factory=list)
    always: set[tuple[str, str]] = field(default_factory=set)  # actions autorisées « toujours »


def _locked[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Méthode du daemon appelée depuis un autre thread : elle prend le verrou de son état."""

    @wraps(method)
    def run(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return run


class Daemon:
    """Le daemon est appelé depuis plusieurs threads : messages (socket), surveillance,
    événements Hyprland, GTK, et celui de la réponse. Son état (session, travail en cours,
    badge, agents…) est protégé par un seul verrou, pris par chaque point d'entrée
    (`_locked`) et jamais gardé pendant une attente : réponse de Claude, ton accord."""

    def __init__(self, config: Config, ui: UI) -> None:
        self.config = config
        self.ui = ui
        # Tes commandes « / » ([commands] de la config), en plus de celles de Custos.
        self.commands = [slash.custom(name, c.prompt, c.fast) for name, c in config.commands.items()]
        self.session: Session | None = None
        self.last_failure: dict | None = None  # dernière commande shell en échec
        self._failure_at = 0.0
        self._output_requested_at: float | None = None
        self.notice: Notice | None = None
        self.hidden = False  # mascotte masquée par une fenêtre en plein écran
        self.monitor: str | None = None  # écran où se trouve la mascotte
        self.attached: str | None = None  # adresse de la fenêtre où la mascotte est accrochée
        self._last_selection_hash: int | None = None
        self._busy = threading.Lock()  # une seule question à Claude à la fois
        self._cancel = threading.Event()  # levé quand on ferme la carte : la réponse s'arrête
        self._closed_at = 0.0
        self._machine: str | None = None  # détectée à la première question
        self.agents: dict[str, Agent] = {}  # sessions Claude Code, par identifiant
        self.work: Work | None = None  # question en cours de traitement
        self.card_open = False  # la carte est affichée (sinon le travail continue en fond)
        # Problèmes remarqués et pas encore regardés, par clé (voir system.Problem).
        self.problems: dict[str, system.Problem] = {}
        self._code_errors: dict[str, int] = {}  # erreurs par fichier, au dernier enregistrement
        self._lock = threading.RLock()  # l'état ci-dessus (voir la docstring de la classe)

    def handle(self, message: dict) -> None:
        match message.get("type"):
            case "ask":
                self.ask(message)
            case "shell-event":
                self.shell_event(message)
            case "shell-output":
                self.shell_output(message)
            case "agent-event":
                self.agent_event(message)
            case "code-problem":
                self.code_problem(message)
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
        with self._lock:
            question = self._open(message)
        if question:
            self.answer(question)  # sans le verrou : la réponse peut être longue

    def _open(self, message: dict) -> str | None:
        """Lit le contexte et ouvre la carte ; renvoie la question à poser, s'il y en a une."""
        if self.work:
            self._show_work()  # il travaille encore : la carte reprend là où il en est
            return None
        if self._can_resume(message):
            self._reopen()
            return None
        image = message.get("image")
        target = self._attached_window(message)
        ctx = context.collect(
            self.config.privacy,
            selection=bool(message.get("selection")),
            screenshot=bool(message.get("screen")) or target is not None,
            failure=message.get("failure"),
            problem=message.get("problem"),
            image=Path(image) if image else None,
            image_kind=message.get("image_kind", "window"),
            window=target,
            attached=target is not None,
        )
        if ctx.blocked:
            self.ui.show_error("Cette fenêtre est protégée : je n'ai rien lu.")
            return None

        self.close_session()
        self.session = Session(ctx, summary=context.describe(ctx))
        if message.get("question"):
            return message["question"]
        self.card_open = True
        self.ui.set_history([])
        self.ui.set_attachments(0)
        self.ui.open_prompt(self.session.summary, context.hint(ctx))
        return None

    def _reopen(self) -> None:
        session = self.session
        log.info("conversation reprise")
        self.card_open = True
        if self.notice and self.notice.kind == ANSWER_READY:
            self.notice = None
            self._show_badge()
        self.ui.set_history(session.exchanges[:-1])
        self.ui.reopen(session.summary, session.last_question, session.last_answer)

    def _show_work(self) -> None:
        work = self.work
        self.card_open = True
        self._show_badge()
        permission = work.permission.description if work.permission else ""
        self.ui.show_progress(work.question, work.status, "".join(work.chunks), permission)

    def _can_resume(self, message: dict) -> bool:
        fresh = ("question", "selection", "screen", "image", "failure", "problem", "new")
        session = self.session
        return (
            not any(message.get(key) for key in fresh)
            and not self.attached  # accrochée : une nouvelle capture de la fenêtre à chaque fois
            and session is not None
            and bool(session.claude_session and session.last_answer)
            and time.monotonic() - self._closed_at < RESUME_WINDOW
        )

    @_locked
    def new_conversation(self) -> None:
        """Bouton « + » : oublie la conversation en cours et en commence une nouvelle."""
        self.close_session()
        self.ask({"type": "ask", "new": True})

    @_locked
    def close_bubble(self) -> None:
        """Carte fermée : le travail en cours continue en fond (« arrêter » l'arrête), et la
        conversation reste un moment pour la reprendre."""
        self.card_open = False
        self._closed_at = time.monotonic()
        if self.work:
            self._show_badge()  # badge orange s'il attend ton accord
            return
        session = self.session
        if session and not session.claude_session:
            self.close_session()  # rien à reprendre : on supprime aussi la capture

    def history_items(self, query: str) -> list[tuple[str, str, str]]:
        """`/h …` dans la carte : (session, quand, première question) des plus récentes."""
        return [(c.session, history.when(c.updated), c.title) for c in history.search(query)]

    @_locked
    def open_history(self, session_id: str) -> None:
        """Conversation choisie dans `/h` : rouverte dans la carte, prête pour la suite."""
        if self.work:
            self._show_work()
            return
        found = history.find(session_id)
        if found is None:
            self.ui.show_error("Je ne retrouve plus cette conversation.")
            return
        self.close_session()
        self.session = Session(
            Context(),
            claude_session=found.session,
            summary=f"reprise · {history.when(found.updated)}",
            last_question=found.last_question,
            last_answer=found.last_answer,
            exchanges=list(found.exchanges),
        )
        self._reopen()

    def stop(self) -> None:
        """Bouton « arrêter » : arrête la réponse ou la tâche en cours."""
        self._cancel.set()

    @_locked
    def permission_answer(self, choice: str) -> None:
        """Ta réponse à une demande de permission (boutons de la carte)."""
        work = self.work
        if work and work.permission:
            work.choice = choice
            work.answered.set()

    @_locked
    def open_from_mascot(self) -> None:
        """Clic sur la mascotte : propose d'abord ce qu'elle vient de remarquer.

        Par ordre de priorité : une commande qui vient d'échouer, un problème de la machine
        (service, disque, mise à jour, plantage, code), une sélection ou une image remarquée,
        sinon une bulle vide. Avant tout ça : un agent Claude Code qui attend ou qui a fini,
        dont elle affiche la fenêtre.
        """
        if self.work:
            self._show_work()  # sa propre tâche d'abord : elle attend peut-être ton accord
            return
        waiting = self._waiting_agent()
        if waiting:
            self._go_to_agent(waiting)  # le plus urgent : un agent est bloqué
            return
        if self.notice and self.notice.kind == ANSWER_READY and self.session:
            self._reopen()  # la réponse finie pendant que la carte était fermée
            return
        now = time.monotonic()
        failure = self.last_failure if now - self._failure_at < RECENT_FAILURE else None
        notice = self.notice if self.notice and self._fresh(self.notice, now) else None
        self.last_failure = None
        self._clear_notice()

        if notice and notice.kind == AGENT_DONE:
            agent = self.agents.get(notice.path or "")
            if agent:
                self._go_to_agent(agent)
            return
        if failure:
            # La fenêtre active est sans doute le terminal : sa capture montre le message d'erreur.
            self.ask({"type": "ask", "screen": True, "failure": failure})
        elif self.problems:
            self.open_problem(next(reversed(self.problems)))  # le plus récent
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

    @_locked
    def on_watch_event(self, kind: str, path: str | None = None) -> None:
        """Changement de sélection, image copiée ou capture : allume le badge (sans appeler Claude)."""
        # Carte ouverte : on ne dérange pas (et sa propre sélection ne compte pas). La
        # session, elle, reste après la fermeture pour la reprendre : elle ne bloque rien.
        if self.card_open or self._busy.locked():
            return
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
        self._show_badge()

    def _fresh(self, notice: Notice, now: float) -> bool:
        # « Agent fini » reste jusqu'à ce que tu ailles voir ; le reste s'éteint tout seul.
        return notice.kind in (AGENT_DONE, ANSWER_READY) or now - notice.at < self.config.notice.duration

    def _attached_window(self, message: dict) -> hyprland.ActiveWindow | None:
        """Fenêtre accrochée à lire, pour une question « simple » (sans sélection, image ni erreur)."""
        if not self.attached or any(message.get(k) for k in ("selection", "image", "failure")):
            return None
        return hyprland.find_window(self.attached)

    @_locked
    def attach_active(self) -> None:
        """`custos attach` : accroche la mascotte à la fenêtre active."""
        window = hyprland.active_window()
        if window is None or not window.address:
            self.ui.show_error("Aucune fenêtre active à laquelle m'accrocher.")
            return
        self.ui.attach(window)  # l'overlay rappelle set_attached une fois accrochée

    @_locked
    def set_attached(self, window: hyprland.ActiveWindow | None) -> None:
        """Appelé par l'overlay quand la mascotte s'accroche ou se détache."""
        if window and privacy.is_blocked(window.window_class, window.title, self.config.privacy):
            self.ui.detach()
            self.ui.show_error("Cette fenêtre est protégée : je ne m'y accroche pas.")
            return
        self.attached = window.address if window else None
        log.info("accrochée à une fenêtre" if window else "détachée")

    @_locked
    def set_monitor(self, name: str) -> None:
        """Appelé par l'overlay quand la mascotte change d'écran."""
        self.monitor = name
        self.on_hyprland_event("fullscreen")

    @_locked
    def on_hyprland_event(self, name: str, data: str = "") -> None:
        """Masque la mascotte tant qu'une fenêtre est en plein écran (vidéo, jeu…) sur son écran.

        Suit aussi les écrans branchés ou débranchés.
        """
        if name in MONITOR_EVENTS:
            self.ui.monitors_changed()
        if name == "activewindowv2":
            self._seen_done_agent(data)
        if name not in FULLSCREEN_EVENTS or not self.config.mascot.hide_in_fullscreen:
            return
        if self.monitor:
            hidden = hyprland.fullscreen_on(self.monitor)
        else:
            hidden = hyprland.active_is_fullscreen()
        if hidden != self.hidden:
            self.hidden = hidden
            log.info("plein écran : mascotte %s", "masquée" if hidden else "réaffichée")
            self.ui.set_hidden(hidden)

    def _clear_notice(self) -> None:
        if self.notice:
            self.notice = None
            self._show_badge()

    def _show_badge(self) -> None:
        """Badge de la mascotte, et alerte de l'icône de la barre (pour quand la mascotte
        est rangée ou cachée) : un agent qui attend passe avant tout le reste."""
        work, notice = self.work, self.notice
        waiting = self._waiting_agent()
        alert, text = None, ""
        if work and work.permission and not self.card_open:
            kind = CUSTOS_WAITING
            alert, text = ALERT_WAITING, f"Custos attend ton accord : {_short(work.permission.description)}"
        elif waiting:
            kind = AGENT_WAITING
            alert, text = ALERT_WAITING, f"Claude Code ({waiting.project}) attend ta permission"
        elif self.problems:
            kind = SYSTEM_PROBLEM
            latest = next(reversed(self.problems.values()))
            more = len(self.problems) - 1
            alert, text = ALERT_PROBLEM, _short(latest.title, 70) + (f" (+{more})" if more else "")
        else:
            kind = notice.kind if notice else None
            agent = self.agents.get(notice.path or "") if notice else None
            if kind == AGENT_DONE and agent:
                alert, text = ALERT_DONE, f"Claude Code ({agent.project}) a fini"
            elif kind == ANSWER_READY and self.session:
                alert, text = ALERT_DONE, f"Custos a fini : {_short(self.session.last_question)}"
        self.ui.show_notice(kind)
        self.ui.set_alert(alert, text)

    # --- problèmes de la machine (voir system.py) ------------------------------------

    @_locked
    def on_problem(self, problem: system.Problem) -> None:
        """Problème remarqué localement : la mascotte s'inquiète, un badge ambre s'allume."""
        self.problems.pop(problem.key, None)
        self.problems[problem.key] = problem  # le plus récent en dernier
        if not self.card_open:
            self.ui.set_state(MascotState.WORRIED)
        self._show_badge()

    @_locked
    def on_resolved(self, key: str) -> None:
        """Le problème a disparu de lui-même (service relancé, place libérée…)."""
        if self.problems.pop(key, None):
            log.info("problème résolu de lui-même")
            self._show_badge()

    def open_problem(self, key: str) -> None:
        """Clic sur la mascotte ou dans le menu : la carte s'ouvre avec ses preuves."""
        with self._lock:
            problem = self.problems.pop(key, None)
            self._show_badge()
        if problem:
            self.ask({"type": "ask", "problem": problem.as_message()})

    @_locked
    def code_problem(self, message: dict) -> None:
        """Erreurs d'un fichier à son enregistrement (extension VS Code).

        Seulement quand leur nombre augmente : la mascotte ne s'inquiète pas d'erreurs déjà
        connues. Plus aucune erreur : l'alerte s'éteint.
        """
        if not self.config.system.code:
            return
        path = str(message.get("file") or "")
        count = int(message.get("count") or 0)
        if not path:
            return
        key = f"code:{path}"
        previous = self._code_errors.get(path, 0)
        self._code_errors[path] = count
        if count == 0:
            self._code_errors.pop(path, None)
            if self.problems.pop(key, None):
                self._show_badge()
            return
        if count <= previous:
            return
        lines = []
        for error in (message.get("errors") or [])[:MAX_CODE_ERRORS]:
            where = f"ligne {error.get('line', '?')}"
            source = f" [{error['source']}]" if error.get("source") else ""
            lines.append(f"- {where}{source} : {error.get('message', '')}")
        details = f"Fichier : {path}\n"
        if message.get("workspace"):
            details += f"Projet : {message['workspace']}\n"
        details += f"Erreurs signalées par VS Code ({count}) :\n" + "\n".join(lines)
        plural = "s" if count > 1 else ""
        title = f"{count} erreur{plural} dans {Path(path).name}"
        log.info("erreurs dans le code (%d)", count)
        self.problems.pop(key, None)
        self.problems[key] = system.Problem(system.CODE, key, title, details)
        if not self.card_open:
            self.ui.set_state(MascotState.WORRIED)
        self._show_badge()

    # --- sessions Claude Code ------------------------------------------------------

    @_locked
    def agent_event(self, message: dict) -> None:
        """Hook d'une session Claude Code : elle travaille, attend, a fini ou se ferme."""
        if not self.config.agents.enabled:
            return
        session = message.get("session") or ""
        cwd = message.get("cwd") or ""
        if not session or Path(cwd) == claude.conversations_dir():
            return  # la conversation de Custos ouverte en grand : elle a déjà sa bulle
        event = message.get("event")
        if event == "SessionEnd":
            self.agents.pop(session, None)
            self._show_badge()
            return
        state = {"UserPromptSubmit": "working", "PostToolUse": "working", "Stop": "done"}.get(event)
        if event == "Notification":
            kind = message.get("notification") or ""
            text = (message.get("message") or "").lower()
            if kind in WAITING_NOTIFICATIONS or (not kind and "permission" in text):
                state = "waiting"
        if state is None:
            return
        previous = self.agents.get(session)
        if previous and previous.state == state:
            return  # PostToolUse arrive à chaque outil : rien de neuf
        pids = [int(p) for p in message.get("pids") or [] if str(p).isdigit()]
        agent = Agent(session, Path(cwd).name or "Claude Code", state, pids, time.monotonic())
        self.agents[session] = agent
        log.info("agent %s : %s", agent.project, state)
        if state == "done":
            # Comme une sélection remarquée : un clic y mène, le badge s'éteint tout seul.
            self.notice = Notice(AGENT_DONE, agent.at, session)
        elif self.notice and self.notice.kind == AGENT_DONE and self.notice.path == session:
            self.notice = None  # elle s'est remise au travail
        self._show_badge()

    def _seen_done_agent(self, address: str) -> None:
        """Tu passes sur la fenêtre de l'agent qui a fini : son badge s'éteint."""
        notice = self.notice
        if not notice or notice.kind != AGENT_DONE:
            return
        agent = self.agents.get(notice.path or "")
        window = hyprland.window_of(agent.pids) if agent else None
        if agent is None or (window and window.address == f"0x{address.removeprefix('0x')}"):
            self._clear_notice()

    def _waiting_agent(self) -> Agent | None:
        """Le plus ancien agent qui attend encore (ceux dont Claude Code a disparu sont oubliés)."""
        for session, agent in list(self.agents.items()):
            if not agent.alive():
                del self.agents[session]
        waiting = [a for a in self.agents.values() if a.state == "waiting"]
        return min(waiting, key=lambda a: a.at) if waiting else None

    def _go_to_agent(self, agent: Agent) -> None:
        """Clic sur la mascotte : t'emmène dans la fenêtre de cet agent."""
        window = hyprland.window_of(agent.pids)
        if window and window.address and hyprland.focus_window(window.address):
            log.info("fenêtre de l'agent %s affichée", agent.project)
            return
        what = "attend ta réponse" if agent.state == "waiting" else "a fini"
        self.ui.show_error(f"Claude Code ({agent.project}) {what}, mais je ne trouve pas sa fenêtre.")

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
        """Pose la question à Claude et affiche la réponse au fil de l'eau (dans son propre
        thread : appelée sans le verrou, elle ne le prend que pour toucher à l'état)."""
        with self._lock:
            started = self._start_work(question)
        if started is None:
            return
        session, work, prompt, images, agent, model = started
        try:
            begin = time.monotonic()

            def remember(session_id: str) -> None:
                with self._lock:
                    session.claude_session = session_id

            def on_tool(step: str) -> None:
                # Le texte d'avant n'était qu'un commentaire : la bulle ne garde que la conclusion.
                with self._lock:
                    work.status, work.chunks = step, []
                    self.ui.reset_answer()
                    self.ui.set_status(step)

            for chunk in claude.ask(
                prompt,
                self.config.claude,
                images=images,
                system_prompt=self._system_prompt(agent),
                resume=session.claude_session,
                on_session=remember,
                cancel=self._cancel,
                agent=agent,
                on_tool=on_tool,
                on_permission=lambda permission: self._ask_permission(session, work, permission),
                model=model,
                on_rate_limit=self._on_rate_limit,
            ):
                with self._lock:
                    work.chunks.append(chunk)
                    self.ui.append_answer(chunk)
            with self._lock:
                self._end_work(session, work, begin)
        except claude.ClaudeError as exc:
            log.warning("erreur Claude : %s", exc)
            with self._lock:
                self.ui.set_state(MascotState.IDLE)
                self.ui.show_error(str(exc))
        finally:
            with self._lock:
                self.work = None
                self._busy.release()
                self._show_badge()

    def _start_work(self, question: str):
        """Début d'une réponse (sous le verrou) : de quoi interroger Claude, ou None si
        la question est vide ou qu'il réfléchit déjà."""
        session = self.session or Session(Context())
        self.session = session
        ctx = session.context
        # `/vite …` : cette question seulement, avec le modèle rapide (fast_model).
        model = None
        shown = ""  # ce que la carte affiche, quand ce n'est pas la question envoyée
        parsed = slash.parse(question.strip(), self.commands)
        if parsed and parsed[0].action == slash.REPLAY:
            # `/r` : la dernière question, avec le même modèle (même si elle a échoué).
            if session.last_asked is None:
                self.ui.show_error("Pas encore de question à reposer.")
                return None
            question, model, shown = session.last_asked
            parsed = None
        if parsed and parsed[0].action == slash.FAST:
            model, question = self.config.claude.fast_model, parsed[1]
            if not question and not context.default_question(ctx):
                self.ui.show_error("Écris ta question après /vite.")
                return None
        if parsed and parsed[0].action == slash.CUSTOM:
            # Une des tiennes : sa consigne est envoyée, la carte garde « /traduis … ».
            command, rest = parsed
            shown, question = question.strip(), slash.expand(command, rest)
            model = self.config.claude.fast_model if command.fast else None
        question = question.strip() or context.default_question(ctx) or ""
        if not question:
            self.ui.show_error("Écris ta question.")
            return None
        if not self._busy.acquire(blocking=False):
            self.ui.show_error("Je réfléchis déjà à ta question précédente.")
            return None
        self._cancel.clear()
        session.last_asked = (question, model, shown)
        shown = shown or question
        work = self.work = Work(shown)
        agent = self.config.claude.tools and getattr(self.ui, "interactive", True)
        # Première question : avec le contexte (fenêtre, sélection, capture). Les suivantes
        # reprennent la session Claude, qui a déjà tout ça : juste la question.
        resume = session.claude_session
        prompt = question if resume else context.build_prompt(question, ctx)
        if resume and ctx.attachments:
            prompt = f"{context.ATTACHMENT_NOTE.format(count=len(ctx.attachments))}\n\n{prompt}"
        images = ctx.images(first=not resume) or None
        log.info(
            "question (sélection : %d car., images : %d, suivi : %s, outils : %s, modèle : %s)",
            len(ctx.selection),
            len(images or []),
            "oui" if resume else "non",
            "oui" if agent else "non",
            model or self.config.claude.model,
        )
        self.ui.set_state(MascotState.THINKING)
        self.card_open = True
        self.ui.set_history(session.exchanges)
        self.ui.set_attachments(0)  # envoyées avec cette question
        self.ui.start_answer(f"{shown} · {short_model(model)}" if model else shown)
        return session, work, prompt, images, agent, model

    def _end_work(self, session: Session, work: Work, begin: float) -> None:
        """Réponse finie ou interrompue (sous le verrou)."""
        session.context.cleanup()  # la capture n'est envoyée qu'avec la première question
        if self._cancel.is_set():
            log.info("réponse interrompue")
            self.ui.set_state(MascotState.IDLE)
            self.ui.end_answer("".join(work.chunks).strip() or "Arrêté.")
            return
        answer = "".join(work.chunks).strip()
        log.info("réponse en %.1f s (%d car.)", time.monotonic() - begin, len(answer))

        session.last_question, session.last_answer = work.question, answer
        if answer:
            session.exchanges.append((work.question, answer))
        self.ui.end_answer(answer)
        self.ui.set_state(MascotState.TALKING if self.card_open else MascotState.IDLE)
        if not self.card_open:
            # Finie carte fermée : ✅ jusqu'à ce que tu cliques pour la lire.
            self.notice = Notice(ANSWER_READY, time.monotonic())

    def _on_rate_limit(self, info: dict) -> None:
        """Quota annoncé par Claude Code pendant la réponse : pour l'info-bulle de l'icône."""
        current = quota.Quota.from_event(info)
        if current is None:
            return
        quota.save(current)
        self.ui.set_quota(current)

    def show_saved_quota(self) -> None:
        """Au démarrage : le dernier quota connu, en attendant la prochaine question."""
        self.ui.set_quota(quota.load())

    def _ask_permission(self, session: Session, work: Work, permission: claude.Permission) -> str:
        """Action sensible : on attend ta réponse dans la carte (badge orange sur la mascotte si elle
        est fermée). Appelé dans le thread de la réponse, qui attend ici, sans le verrou."""
        key = (permission.tool, permission.description)
        with self._lock:
            if key in session.always:
                return claude.ALLOW
            log.info("permission demandée (%s)", permission.tool)
            work.answered.clear()
            work.permission = permission
            self.ui.ask_permission(permission.description)
            self._show_badge()
        while not work.answered.wait(0.2):
            if self._cancel.is_set():
                work.choice = claude.DENY
                break
        with self._lock:
            work.permission = None
            self.ui.permission_done()
            self._show_badge()
            if work.choice == claude.ALWAYS:
                session.always.add(key)
            log.info("permission : %s", work.choice)
            return work.choice

    def _system_prompt(self, agent: bool = False) -> str:
        """Machine détectée une fois ; custos.md relu à chaque question (modifiable à chaud)."""
        if self._machine is None:
            self._machine = machine.describe(self.config.claude.open_command)
        return claude.build_system_prompt(self._machine, load_persona(), agent)

    @_locked
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

    def tray_menu(self) -> list:
        """Menu de l'icône de la barre (voir tray.Menu) : poser une question, conversations
        récentes, sessions Claude Code. Les actions partent dans un thread : elles lisent le
        contexte ou des fichiers, et le menu est appelé depuis le thread GTK."""
        entries: list = [("Poser une question", _in_thread(self.ask, {"type": "ask"}))]
        with self._lock:
            problems = list(reversed(self.problems.values()))
        if problems:
            entries += [None, ("À regarder", None)]
            entries += [
                (_short(p.title, 50), _in_thread(self.open_problem, p.key), ("alert", "#d9a33a"))
                for p in problems
            ]
            entries.append(None)
        recent = history.search("", limit=MENU_HISTORY)
        if recent:
            entries.append((
                "Conversations récentes",
                [
                    (f"{history.when(c.updated)} · {_short(c.title, 40)}", _in_thread(self.open_history, c.session))
                    for c in recent
                ],
            ))
        with self._lock:
            self._waiting_agent()  # oublie les sessions dont Claude Code a disparu
            agents = sorted(self.agents.values(), key=lambda a: a.at, reverse=True)
        if agents:
            entries += [None, ("Sessions Claude Code", None)]
            for a in agents:
                state, icon = AGENT_STATES.get(a.state, (a.state, ("sparkle", "#9e9e9e")))
                entries.append((f"{a.project} · {state}", _in_thread(self.go_to_agent, a.session), icon))
        return entries

    @_locked
    def go_to_agent(self, session: str) -> None:
        """Menu de l'icône : la fenêtre de cette session Claude Code."""
        agent = self.agents.get(session)
        if agent:
            self._go_to_agent(agent)

    def add_image(self, source: Path, temporary: bool = False) -> None:
        """Image collée dans la carte ou glissée sur la mascotte (hors du thread GTK).

        Carte ouverte : jointe à ta prochaine question. Sinon, la carte s'ouvre avec elle.
        `temporary` : `source` est une copie à supprimer (presse-papier, image glissée d'un
        navigateur), pas un de tes fichiers.
        """
        image = images.prepare(source)
        if temporary:
            source.unlink(missing_ok=True)
        if image is None:
            self.ui.show_error("Je ne sais pas lire cette image.")
            return
        with self._lock:
            if self.work:
                image.unlink(missing_ok=True)
                self.ui.show_error("Attends la fin de la réponse pour joindre une image.")
                return
            session = self.session
            if self.card_open and session:
                session.context.attachments.append(image)
                self.ui.set_attachments(len(session.context.attachments))
                log.info("image jointe (%d)", len(session.context.attachments))
                return
        self.ask({"type": "ask", "image": str(image), "image_kind": "dropped"})

    def paste_image(self) -> None:
        """Ctrl+V dans la carte avec une image dans le presse-papier."""
        copy = clipboard.image_to_file()
        if copy is None:
            self.ui.show_error("Je n'arrive pas à lire l'image du presse-papier.")
            return
        self.add_image(copy, temporary=True)

    @_locked
    def remove_attachments(self) -> None:
        """Clic sur l'icône d'image de la carte : les images jointes sont retirées."""
        if self.session:
            for path in self.session.context.attachments:
                path.unlink(missing_ok=True)
            self.session.context.attachments = []
        self.ui.set_attachments(0)

    def run_in_terminal(self, command: str) -> None:
        """Clic sur une commande de la réponse : un terminal s'ouvre avec elle, prête à lancer."""
        try:
            terminal.open_with(command, self.config.claude.open_command)
        except OSError as exc:
            self.ui.show_error(f"Impossible d'ouvrir le terminal : {exc}")
            return
        log.info("commande ouverte dans un terminal")

    @_locked
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

    @_locked
    def _request_output(self, address: str) -> None:
        """Demande au terminal la sortie de la dernière commande (réponse : `shell-output`).

        Rien n'est envoyé à Claude ici : la sortie n'est jointe que si tu demandes de l'aide.
        """
        self._output_requested_at = time.monotonic()
        if not hyprland.send_shortcut(*OUTPUT_SHORTCUT, address):
            log.warning("impossible de demander la sortie de la commande au terminal")

    @_locked
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


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def short_model(model: str) -> str:
    """« claude-sonnet-5-5 » → « sonnet » : affiché à côté de la question."""
    parts = model.removeprefix("claude-").split("-")
    return parts[0] if parts else model


def _in_thread(action, *args) -> Callable[[], None]:
    """Action du menu, lancée hors du thread GTK."""
    return lambda: threading.Thread(target=action, args=args, daemon=True).start()


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
    _report_config_problems(config)

    handler: list = []  # rempli une fois le daemon créé
    try:
        server = ipc.Server(lambda message: handler[0](message))
    except ipc.DaemonAlreadyRunning:
        log.error("un daemon Custos tourne déjà")
        return EXIT_ALREADY_RUNNING
    log.info("à l'écoute sur %s", server.path)

    app = _gtk_app(config)
    watcher = system_watcher = None
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
            system_watcher = system.SystemWatcher(config.system, daemon.on_problem, daemon.on_resolved)
            system_watcher.start()
            threading.Thread(
                target=hyprland.listen, args=(daemon.on_hyprland_event, stop_events), daemon=True
            ).start()
            daemon.on_hyprland_event("fullscreen")  # déjà en plein écran au démarrage ?
            daemon.show_saved_quota()
            _serve_with_gtk(server, app)
    finally:
        stop_events.set()
        if watcher:
            watcher.stop()
        if system_watcher:
            system_watcher.stop()
        server.server_close()
        log.info("arrêté")
    return 0


def _report_config_problems(config: Config) -> None:
    """Options ignorées dans config.toml : dans le log, et une notification pour le savoir."""
    if not config.problems:
        return
    for problem in config.problems:
        log.warning("config : %s", problem)
    from custos.ui.notify import notify

    shown = "\n".join(config.problems[:5]) + ("\n…" if len(config.problems) > 5 else "")
    notify("Custos — config.toml", shown)


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
