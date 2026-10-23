"""Communication entre les commandes `custos` et le daemon (socket Unix, JSON)."""

import os
from pathlib import Path


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "custos.sock"


def send(message: dict) -> None:
    """Envoie un message au daemon en cours d'exécution."""
    raise NotImplementedError("ipc.send")


def serve(handler) -> None:
    """Écoute le socket et appelle `handler(message)` pour chaque message reçu."""
    raise NotImplementedError("ipc.serve")
