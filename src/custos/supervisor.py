"""Superviseur : `custos daemon` lance le vrai daemon et le relance s'il plante.

Il écrit aussi le fichier de log, avec les lignes du daemon qu'il reçoit par un tube, et
le fichier PID utilisé par `custos stop`, `restart` et `status`. Seul écrivain du log, il
peut le renouveler à tout moment, même si Custos tourne des semaines.

Les logs ne contiennent jamais le contenu de tes questions, sélections ou
commandes : seulement des événements, des tailles et des durées.
"""

import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("custos.supervisor")

LAYER_SHELL_LIB = Path("/usr/lib/libgtk4-layer-shell.so")
GSK_RENDERER = "gl"  # moteur de rendu GTK : OpenGL
MAX_LOG_SIZE = 1024 * 1024  # au-delà, le log devient custos.log.1 (l'ancien .1 est supprimé)
CRASH_WINDOW = 60.0  # secondes
MAX_CRASHES = 5  # plantages tolérés dans CRASH_WINDOW avant d'abandonner
EXIT_ALREADY_RUNNING = 3  # code du daemon quand un autre tourne déjà : inutile de relancer

LOG_FORMAT = "%(asctime)s %(levelname)s [{role}] %(message)s"


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "custos"


def log_path() -> Path:
    return state_dir() / "custos.log"


def pid_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "custos.pid"


class _Formatter(logging.Formatter):
    """Lignes du superviseur mises en forme ; celles du daemon, déjà prêtes, telles quelles."""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage() if getattr(record, "raw", False) else super().format(record)


def setup_logging(role: str, to_file: bool) -> logging.Handler:
    """Le superviseur écrit le fichier de log (renouvelé au-delà de MAX_LOG_SIZE) ; le
    daemon écrit sur sa sortie d'erreur, que le superviseur y recopie."""
    if to_file:
        log_path().parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=MAX_LOG_SIZE, backupCount=1, encoding="utf-8"
        )
    else:
        handler = logging.StreamHandler()
    handler.setFormatter(_Formatter(LOG_FORMAT.format(role=role)))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    return handler


def _copy_output(stream, handler: logging.Handler) -> None:
    """Recopie dans le log les lignes du daemon (ses logs, ses erreurs Python ou GTK)."""
    for line in stream:
        handler.handle(logging.makeLogRecord({"msg": line.rstrip("\n"), "raw": True}))


def worker_command() -> list[str]:
    return [sys.executable, "-m", "custos", "daemon", "--worker"]


def worker_env() -> dict[str, str]:
    # gtk4-layer-shell doit être chargé avant libwayland, d'où LD_PRELOAD dès le lancement.
    env = dict(os.environ)
    preload = env.get("LD_PRELOAD", "")
    if LAYER_SHELL_LIB.exists() and "libgtk4-layer-shell" not in preload:
        env["LD_PRELOAD"] = f"{LAYER_SHELL_LIB} {preload}".strip()
    # Rendu OpenGL plutôt que Vulkan (défaut de GTK 4.24) : la mascotte redessine en
    # continu, et Vulkan (Mesa ANV, i915) figeait tout le PC. GSK_RENDERER=vulkan pour revenir.
    env.setdefault("GSK_RENDERER", GSK_RENDERER)
    return env


def read_pid() -> int | None:
    """PID du superviseur en cours, ou None (fichier absent ou processus disparu)."""
    try:
        pid = int(pid_path().read_text())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


def supervise(command: list[str] | None = None, backoff: float = 1.0) -> int:
    handler = setup_logging("superviseur", to_file=True)
    if read_pid():
        log.error("Custos tourne déjà")
        return 1

    command = command or worker_command()
    pid_path().write_text(str(os.getpid()))
    stopping = threading.Event()
    child: subprocess.Popen | None = None

    def stop(*_args) -> None:
        stopping.set()
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    crashes: deque[float] = deque()
    log.info("démarrage (PID %d)", os.getpid())
    try:
        while not stopping.is_set():
            child = subprocess.Popen(
                command,
                env=worker_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            # Dans un thread : un processus lancé par le daemon (wl-copy reste en fond) peut
            # garder le tube ouvert après sa mort, sans qu'on attende sa fin pour relancer.
            output = threading.Thread(target=_copy_output, args=(child.stdout, handler), daemon=True)
            output.start()
            code = child.wait()
            output.join(1)  # ses dernières lignes : souvent l'erreur qui l'a fait tomber

            if stopping.is_set() or code == 0:
                break
            if code == EXIT_ALREADY_RUNNING:
                log.error("un autre daemon Custos tourne déjà : arrêt du superviseur")
                return 1

            now = time.monotonic()
            crashes.append(now)
            while crashes and now - crashes[0] > CRASH_WINDOW:
                crashes.popleft()
            if len(crashes) > MAX_CRASHES:
                log.error("%d plantages en %d s : abandon", len(crashes), CRASH_WINDOW)
                _notify_gave_up()
                return 1

            delay = backoff * len(crashes)
            log.warning("le daemon s'est arrêté (code %s) : relance dans %.0f s", code, delay)
            stopping.wait(delay)
    finally:
        pid_path().unlink(missing_ok=True)
        log.info("arrêté")
    return 0


def _notify_gave_up() -> None:
    from custos.ui.notify import notify

    notify("Custos", f"Custos a planté plusieurs fois et s'est arrêté. Voir {log_path()}")


# --- commandes start / stop / status ------------------------------------------


def start() -> int:
    if read_pid():
        print("Custos tourne déjà.")
        return 0
    # Détaché du terminal : le superviseur continue si tu fermes la fenêtre.
    subprocess.Popen(
        [sys.executable, "-m", "custos", "daemon"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(50):
        if read_pid():
            print("Custos démarré.")
            return 0
        time.sleep(0.1)
    print(f"Custos n'a pas démarré : voir {log_path()}")
    return 1


def stop() -> int:
    pid = read_pid()
    if pid is None:
        print("Custos ne tourne pas.")
        return 0
    os.kill(pid, signal.SIGTERM)
    for _ in range(100):
        if read_pid() is None:
            print("Custos arrêté.")
            return 0
        time.sleep(0.1)
    print(f"Custos ne s'arrête pas (PID {pid}).")
    return 1


def restart() -> int:
    code = stop()
    return code or start()


def status() -> int:
    pid = read_pid()
    if pid is None:
        print("Custos ne tourne pas.")
        print(f"Log : {log_path()}")
        return 1
    since = time.strftime("%d/%m %H:%M", time.localtime(pid_path().stat().st_mtime))
    print(f"Custos tourne (PID {pid}, depuis le {since}).")
    print(f"Log : {log_path()}")
    return 0


def logs(follow: bool, lines: int) -> int:
    path = log_path()
    if not path.exists():
        print("Pas encore de log.")
        return 1
    # -F : suit le fichier par son nom, même après un renouvellement du log.
    args = ["tail", "-n", str(lines)] + (["-F"] if follow else []) + [str(path)]
    os.execvp("tail", args)
    return 0  # pragma: no cover (execvp ne revient pas)
