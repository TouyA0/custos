"""Surveillance locale de la machine, sans jamais appeler Claude.

Quatre sources, relues régulièrement dans un thread :
- les services systemd en échec (système et utilisateur) ;
- les disques presque pleins ;
- les erreurs de mise à jour dans /var/log/pacman.log, y compris un script d'après
  mise à jour qui échoue alors que pacman se termine « avec succès » ;
- les applis qui plantent (coredumps, `coredumpctl`).

Chaque problème nouveau appelle `on_problem(Problem)` avec ses preuves déjà rassemblées
(état du service, lignes d'erreur…) ; quand il disparaît de lui-même (service relancé,
place libérée), `on_resolved(clé)`. Une alerte par problème : pas de répétition.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from custos.config import SystemConfig

log = logging.getLogger(__name__)

SERVICE, DISK, UPDATE, CRASH, CODE = "service", "disk", "update", "crash", "code"

POLL_INTERVAL = 30.0  # secondes : services, mises à jour, plantages
DISK_INTERVAL = 300.0  # secondes : la place disque bouge lentement
COMMAND_TIMEOUT = 5  # secondes par commande (systemctl, coredumpctl…)
MAX_DETAILS = 6_000  # caractères de preuves par problème
PACMAN_LOG = Path("/var/log/pacman.log")
DISK_RATIO = 0.05  # alerte sous 5 % libres…
DISK_CAP = 10 * 1024**3  # … plafonné à 10 Go (5 % d'un gros disque, c'est encore beaucoup)
DISK_RECOVERY = 1.5  # l'alerte s'éteint une fois revenu à 1,5 fois le seuil
REAL_FILESYSTEMS = {"ext2", "ext3", "ext4", "btrfs", "xfs", "f2fs", "vfat", "exfat", "ntfs3", "zfs"}
UPDATE_ERRORS = ("error", "failed", "undefined symbol", "no such file", "cannot", "fatal")


@dataclass(frozen=True)
class Problem:
    kind: str  # SERVICE, DISK, UPDATE, CRASH ou CODE
    key: str  # identifiant stable : un même problème n'est signalé qu'une fois
    title: str  # en une ligne, en haut de la carte et dans le menu de la barre
    details: str = ""  # preuves locales, jointes à la question si tu cliques

    def as_message(self) -> dict:
        return {"kind": self.kind, "key": self.key, "title": self.title, "details": self.details}


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_DETAILS else text[:MAX_DETAILS] + "\n[… tronqué]"


# --- services ---------------------------------------------------------------------


def failed_units(output: str) -> list[str]:
    """Noms des unités de `systemctl --failed --plain --no-legend`."""
    units = []
    for line in output.splitlines():
        words = line.replace("●", " ").split()
        if words and "." in words[0]:
            units.append(words[0])
    return units


# --- disques ----------------------------------------------------------------------


def real_mounts(proc_mounts: str) -> list[str]:
    """Points de montage des vrais systèmes de fichiers en écriture, un par périphérique."""
    seen, mounts = set(), []
    for line in proc_mounts.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[2] not in REAL_FILESYSTEMS:
            continue
        device, target, options = parts[0], parts[1].replace("\\040", " "), parts[3].split(",")
        if "ro" in options or device in seen:
            continue
        seen.add(device)
        mounts.append(target)
    return mounts


def disk_threshold(total: int) -> int:
    return int(min(total * DISK_RATIO, DISK_CAP))


def disk_low(total: int, free: int) -> bool:
    return total > 0 and free < disk_threshold(total)


def disk_recovered(total: int, free: int) -> bool:
    return free > disk_threshold(total) * DISK_RECOVERY


def _size(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024 or unit == "To":
            return f"{n:.0f} {unit}" if unit in ("o", "Ko") else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return f"{n:.1f} To"


# --- mises à jour -------------------------------------------------------------------


def update_errors(lines: list[str]) -> list[tuple[str, list[str]]]:
    """(commande, lignes d'erreur) de chaque transaction pacman qui en contient.

    Les avertissements ordinaires (fichiers .pacnew) ne comptent pas : seulement les erreurs,
    y compris la sortie d'un script (`[ALPM-SCRIPTLET]`, `[ALPM-HOOK]`) qui a échoué.
    """
    transactions: list[tuple[str, list[str]]] = []
    command, errors = "", []
    for line in lines:
        if "[PACMAN] Running '" in line:
            if errors:
                transactions.append((command, errors))
            command, errors = line.split("[PACMAN] Running ", 1)[1].strip().strip("'"), []
            continue
        message = line.split("] ", 2)[-1] if line.startswith("[") else line
        lowered = message.casefold()
        if "[ALPM] error" in line or "[PACMAN] error" in line or (
            ("[ALPM-SCRIPTLET]" in line or "[ALPM-HOOK]" in line) and any(e in lowered for e in UPDATE_ERRORS)
        ):
            errors.append(line.strip())
    if errors:
        transactions.append((command, errors))
    return transactions


# --- plantages ----------------------------------------------------------------------


def new_crashes(output: str, since_us: int) -> list[dict]:
    """Plantages de `coredumpctl list --json=short` plus récents que `since_us` (µs)."""
    try:
        entries = json.loads(output or "[]")
    except json.JSONDecodeError:
        return []
    return [e for e in entries if isinstance(e, dict) and int(e.get("time", 0)) > since_us]


def signal_name(number: int) -> str:
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"signal {number}"


# --- surveillance -------------------------------------------------------------------


class SystemWatcher:
    def __init__(
        self,
        config: SystemConfig,
        on_problem: Callable[[Problem], None],
        on_resolved: Callable[[str], None],
        run: Callable[[list[str]], str] | None = None,
    ) -> None:
        self.config = config
        self.on_problem = on_problem
        self.on_resolved = on_resolved
        self.run = run or _run
        self._failed: dict[str, set[str]] = {"system": set(), "user": set()}
        self._low_disks: set[str] = set()
        self._log_offset: int | None = None
        self._log_size: int | None = None
        self._crashes_since = int(time.time() * 1_000_000)
        self._disk_checked = 0.0
        self._stop = threading.Event()

    def start(self) -> None:
        if self._log_offset is None and PACMAN_LOG.exists():
            self._log_offset = PACMAN_LOG.stat().st_size  # seulement ce qui arrive ensuite
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.check()
            self._stop.wait(POLL_INTERVAL)

    def check(self) -> None:
        """Un tour de surveillance ; une source en erreur n'empêche pas les autres."""
        checks = [
            (self.config.services, self.check_services),
            (self.config.updates, self.check_updates),
            (self.config.crashes, self.check_crashes),
        ]
        if time.monotonic() - self._disk_checked >= DISK_INTERVAL:
            self._disk_checked = time.monotonic()
            checks.append((self.config.disk, self.check_disks))
        for enabled, check in checks:
            if not enabled:
                continue
            try:
                check()
            except Exception:
                log.exception("surveillance %s en erreur", check.__name__)

    def _emit(self, problem: Problem) -> None:
        log.info("problème remarqué (%s)", problem.kind)
        self.on_problem(problem)

    def check_services(self) -> None:
        for scope in ("system", "user"):
            flag = ["--user"] if scope == "user" else []
            names = set(failed_units(self.run(["systemctl", *flag, "--failed", "--plain", "--no-legend", "--full"])))
            names -= set(self.config.ignore_units)
            for unit in sorted(names - self._failed[scope]):
                status = self.run(["systemctl", *flag, "status", "--no-pager", "--full", "-n", "30", unit])
                where = "service utilisateur" if scope == "user" else "service"
                self._emit(Problem(SERVICE, f"service:{scope}:{unit}", f"{where} en échec : {unit}", _clip(status)))
            for unit in self._failed[scope] - names:
                self.on_resolved(f"service:{scope}:{unit}")
            self._failed[scope] = names

    def check_disks(self, proc_mounts: str | None = None) -> None:
        mounts = real_mounts(proc_mounts if proc_mounts is not None else Path("/proc/mounts").read_text())
        for mount in mounts:
            try:
                usage = shutil.disk_usage(mount)
            except OSError:
                continue
            key = f"disk:{mount}"
            if mount not in self._low_disks and disk_low(usage.total, usage.free):
                self._low_disks.add(mount)
                details = (
                    f"Point de montage : {mount}\nTaille : {_size(usage.total)}\n"
                    f"Libre : {_size(usage.free)} ({usage.free * 100 // max(usage.total, 1)} %)"
                )
                self._emit(Problem(DISK, key, f"disque presque plein : {mount} ({_size(usage.free)} libres)", details))
            elif mount in self._low_disks and disk_recovered(usage.total, usage.free):
                self._low_disks.discard(mount)
                self.on_resolved(key)

    def check_updates(self) -> None:
        try:
            size = PACMAN_LOG.stat().st_size
        except OSError:
            return
        if self._log_offset is None or size < self._log_offset:
            self._log_offset = size if self._log_offset is None else 0  # journal recréé
            return
        if size != self._log_size:
            self._log_size = size  # pacman écrit encore : on attend que ça se calme
            return
        if size == self._log_offset:
            return
        with PACMAN_LOG.open(encoding="utf-8", errors="replace") as f:
            f.seek(self._log_offset)
            lines = f.read().splitlines()
        offset, self._log_offset = self._log_offset, size
        for i, (command, errors) in enumerate(update_errors(lines)):
            details = f"Commande : {command or '?'}\nErreurs dans /var/log/pacman.log :\n" + "\n".join(errors)
            plural = "s" if len(errors) > 1 else ""
            title = f"mise à jour : {len(errors)} erreur{plural}" + (f" ({command})" if command else "")
            self._emit(Problem(UPDATE, f"update:{offset}:{i}", title, _clip(details)))

    def check_crashes(self) -> None:
        since = self._crashes_since
        crashes = new_crashes(
            self.run(["coredumpctl", "list", "--json=short", "--no-pager", f"--since=@{since // 1_000_000}"]),
            since,
        )
        if not crashes:
            return
        self._crashes_since = max(int(c["time"]) for c in crashes)
        latest: dict[str, dict] = {}
        for crash in crashes:  # plusieurs plantages du même programme : le dernier suffit
            latest[crash.get("exe", "?")] = crash
        for exe, crash in latest.items():
            name = Path(exe).name
            info = self.run(["coredumpctl", "info", "--no-pager", str(crash.get("pid", ""))])
            title = f"{name} a planté ({signal_name(int(crash.get('sig', 0)))})"
            self._emit(Problem(CRASH, f"crash:{crash.get('pid')}:{crash.get('time')}", title, _clip(info)))


def _run(argv: list[str]) -> str:
    """Sortie d'une commande, ou "" si elle n'existe pas ou dépasse le délai."""
    if shutil.which(argv[0]) is None:
        return ""
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False,
            env={**os.environ, "SYSTEMD_COLORS": "0", "LC_ALL": "C.UTF-8"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout
