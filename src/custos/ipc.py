"""Communication entre les commandes `custos` et le daemon (socket Unix, JSON).

Une connexion = un message JSON terminé par un retour à la ligne. Le client
n'attend pas de réponse : le daemon répond à l'écran, pas dans le terminal.
"""

import json
import logging
import os
import socket
import socketserver
from collections.abc import Callable
from pathlib import Path

MAX_MESSAGE = 1024 * 1024  # octets

log = logging.getLogger(__name__)


class DaemonNotRunning(Exception):
    pass


class DaemonAlreadyRunning(Exception):
    pass


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "custos.sock"


def send(message: dict) -> None:
    """Envoie un message au daemon en cours d'exécution."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        try:
            sock.connect(str(socket_path()))
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise DaemonNotRunning from exc
        sock.sendall(json.dumps(message).encode() + b"\n")


def _is_alive(path: Path) -> bool:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        try:
            sock.connect(str(path))
        except OSError:
            return False
    return True


class Server(socketserver.ThreadingUnixStreamServer):
    """Serveur du daemon : appelle `handler(message)` pour chaque message, dans un thread."""

    daemon_threads = True

    def __init__(self, handler: Callable[[dict], None], path: Path | None = None) -> None:
        self.path = path or socket_path()
        if self.path.exists():
            if _is_alive(self.path):
                raise DaemonAlreadyRunning(str(self.path))
            self.path.unlink()  # socket laissé par un daemon qui a planté

        self.handler = handler
        super().__init__(str(self.path), _RequestHandler)
        self.path.chmod(0o600)

    def server_close(self) -> None:
        super().server_close()
        self.path.unlink(missing_ok=True)


class _RequestHandler(socketserver.StreamRequestHandler):
    server: Server

    def handle(self) -> None:
        line = self.rfile.readline(MAX_MESSAGE)
        if not line.strip():
            return  # simple test de connexion (« un daemon tourne-t-il déjà ? »)
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            log.warning("message illisible ignoré")
            return
        try:
            self.server.handler(message)
        except Exception:
            log.exception("erreur en traitant %s", message.get("type"))
