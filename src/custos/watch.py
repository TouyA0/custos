"""Surveillance locale de ce que tu pourrais vouloir montrer à Custos.

Trois sources, sans jamais appeler Claude :
- la sélection de texte (`wl-paste --primary --watch`) ;
- le presse-papier, quand il contient une image (`wl-paste --watch`) ;
- les captures de zone de caelestia (`SUPER + SHIFT + S`), enregistrées dans /tmp
  avant d'être ouvertes dans swappy.

Chaque changement appelle `on_event(kind, path)` dans un thread de surveillance.
"""

import glob
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

POLL_INTERVAL = 1.0  # secondes, pour les fichiers de capture
STARTUP_GRACE = 1.5  # wl-paste peut signaler le contenu déjà présent au démarrage

SELECTION = "selection"
CLIPBOARD_IMAGE = "clipboard-image"
SCREENSHOT = "screenshot"


class Watcher:
    def __init__(
        self,
        on_event: Callable[[str, str | None], None],
        selection: bool = True,
        images: bool = True,
        screenshot_glob: str = "",
    ) -> None:
        self.on_event = on_event
        self.selection = selection
        self.images = images
        self.screenshot_glob = screenshot_glob
        self._processes: list[subprocess.Popen] = []
        self._stop = threading.Event()
        self._started = 0.0

    def start(self) -> None:
        self._started = time.monotonic()
        if self.selection:
            self._watch_clipboard(["--primary"], SELECTION)
        if self.images:
            self._watch_clipboard([], CLIPBOARD_IMAGE)
        if self.screenshot_glob:
            pattern = os.path.expanduser(self.screenshot_glob)
            # Lu ici et non dans le thread : une capture faite juste après start() compte.
            existing = set(glob.glob(pattern))
            threading.Thread(target=self._poll_screenshots, args=(pattern, existing), daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for process in self._processes:
            process.terminate()

    def _emit(self, kind: str, path: str | None = None) -> None:
        if time.monotonic() - self._started < STARTUP_GRACE:
            return
        try:
            self.on_event(kind, path)
        except Exception:
            log.exception("erreur en traitant l'événement %s", kind)

    def _watch_clipboard(self, args: list[str], kind: str) -> None:
        # wl-paste lance `echo` à chaque changement : on ne lit que ce signal, pas le contenu.
        try:
            process = subprocess.Popen(
                ["wl-paste", *args, "--watch", "echo", kind],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            log.warning("wl-paste introuvable : surveillance %s désactivée", kind)
            return
        self._processes.append(process)

        def read() -> None:
            for _line in process.stdout:
                if kind == CLIPBOARD_IMAGE and not clipboard_has_image():
                    continue
                self._emit(kind)

        threading.Thread(target=read, daemon=True).start()

    def _poll_screenshots(self, pattern: str, seen: set[str]) -> None:
        while not self._stop.wait(POLL_INTERVAL):
            current = set(glob.glob(pattern))
            for path in sorted(current - seen):
                self._emit(SCREENSHOT, path)
            seen = current


def clipboard_has_image() -> bool:
    try:
        result = subprocess.run(
            ["wl-paste", "--list-types"], capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(t.startswith("image/") for t in result.stdout.split())
