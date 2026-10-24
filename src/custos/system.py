"""Surveillance locale de la machine, sans jamais appeler Claude.

Sources relues régulièrement dans un thread :
- les services systemd en échec (système et utilisateur) ;
- les disques presque pleins ;
- les erreurs de mise à jour dans /var/log/pacman.log, y compris un script d'après
  mise à jour qui échoue alors que pacman se termine « avec succès » ;
- les applis qui plantent (coredumps, `coredumpctl`) ;
- le noyau mis à jour sans redémarrage : les modules du noyau en cours ont disparu de
  /usr/lib/modules, et une clé USB, un VPN… ne marchent plus jusqu'au redémarrage ;
- les fichiers .pacnew laissés dans /etc par une mise à jour, à fusionner ;
- les news Arch publiées depuis ta dernière mise à jour complète, à lire avant la
  prochaine (seule requête réseau de Custos, vers archlinux.org, sans rien envoyer).

Chaque problème nouveau appelle `on_problem(Problem)` avec ses preuves déjà rassemblées
(état du service, lignes d'erreur…) ; quand il disparaît de lui-même (service relancé,
place libérée), `on_resolved(clé)`. Une alerte par problème : pas de répétition.
"""

import difflib
import html
import http.client
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from custos.config import SystemConfig
from custos.supervisor import state_dir

log = logging.getLogger(__name__)

SERVICE, DISK, UPDATE, CRASH, CODE = "service", "disk", "update", "crash", "code"
KERNEL, PACNEW, NEWS = "kernel", "pacnew", "news"

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
PACMAN_LOCK = Path("/var/lib/pacman/db.lck")  # pacman travaille : fichiers en plein changement
MODULES_DIR = Path("/usr/lib/modules")
ETC = Path("/etc")
MAX_PACNEW_DIFF = 1_200  # caractères de différences par fichier .pacnew
NEWS_URL = "https://archlinux.org/feeds/news/"
NEWS_INTERVAL = 6 * 3600.0  # secondes : Arch publie une ou deux news par mois
NEWS_RETRY = 1800.0  # secondes : hors ligne, on réessaie plus tard
NEWS_TIMEOUT = 10  # secondes
MAX_NEWS_TEXT = 3_000  # caractères du texte d'une news
KERNEL_PACKAGE = re.compile(r"\[ALPM\] upgraded (linux(?:-lts|-zen|-hardened|-rt|-rt-lts)?) \(")


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


# --- noyau --------------------------------------------------------------------------


def kernel_missing(release: str, modules_dir: Path) -> bool:
    """Les modules du noyau en cours ont disparu (mis à jour, pas encore redémarré)."""
    return modules_dir.is_dir() and not (modules_dir / release).is_dir()


def kernel_upgrades(lines: list[str]) -> list[str]:
    """Lignes de pacman.log où un paquet de noyau a été mis à jour."""
    return [line.strip() for line in lines if KERNEL_PACKAGE.search(line)]


# --- fichiers .pacnew ---------------------------------------------------------------


def find_pacnew(root: Path) -> set[Path]:
    """Fichiers .pacnew sous `root` ; les dossiers illisibles sans root sont sautés."""
    found = set()
    for folder, _dirs, files in os.walk(root):
        found.update(Path(folder) / name for name in files if name.endswith(".pacnew"))
    return found


def pacnew_diff(pacnew: Path) -> str:
    """Ce qui change entre ta version et la nouvelle, en bref."""
    current = pacnew.with_suffix("")
    try:
        old = current.read_text(errors="replace").splitlines()
        new = pacnew.read_text(errors="replace").splitlines()
    except OSError:
        return "(illisible sans root)"
    diff = "\n".join(difflib.unified_diff(old, new, str(current), str(pacnew), n=1, lineterm=""))
    if not diff:
        return "(identique à ta version : le .pacnew peut simplement être supprimé)"
    return diff if len(diff) <= MAX_PACNEW_DIFF else diff[:MAX_PACNEW_DIFF] + "\n[… tronqué]"


# --- news Arch ----------------------------------------------------------------------


@dataclass(frozen=True)
class News:
    guid: str
    title: str
    link: str
    published: datetime
    text: str


def parse_news(xml_text: str) -> list[News]:
    """Articles du flux RSS des news Arch ; un article mal formé est sauté."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    news = []
    for item in root.iter("item"):
        try:
            published = parsedate_to_datetime(item.findtext("pubDate", ""))
        except (TypeError, ValueError):
            continue
        text = html.unescape(re.sub(r"<[^>]+>", "", item.findtext("description", ""))).strip()
        if len(text) > MAX_NEWS_TEXT:
            text = text[:MAX_NEWS_TEXT] + "\n[… tronqué]"
        link = item.findtext("link", "").strip()
        title = " ".join(item.findtext("title", "").split())
        news.append(News(item.findtext("guid", "").strip() or link, title, link, published, text))
    return news


def last_full_upgrade(lines: list[str]) -> datetime | None:
    """Date de la dernière mise à jour complète (`pacman -Syu`, `yay`…) dans pacman.log."""
    for line in reversed(lines):
        if "[PACMAN] starting full system upgrade" in line and line.startswith("["):
            try:
                return datetime.fromisoformat(line[1 : line.index("]")])
            except ValueError:
                continue
    return None


def _fetch(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "custos"})
    with urllib.request.urlopen(request, timeout=NEWS_TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


# --- ce qui a déjà été signalé, d'une session à l'autre -----------------------------


def memory_path() -> Path:
    return state_dir() / "system.json"


def load_memory(path: Path | None = None) -> dict[str, list[str]]:
    """Fichiers .pacnew et news déjà signalés : pas de nouvelle alerte à chaque démarrage."""
    try:
        data = json.loads((path or memory_path()).read_text())
    except (OSError, ValueError):
        return {}
    return {k: [str(v) for v in data[k]] for k in (PACNEW, NEWS) if isinstance(data.get(k), list)}


def save_memory(memory: dict[str, list[str]], path: Path | None = None) -> None:
    path = path or memory_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(memory, ensure_ascii=False, indent=1))
    except OSError:
        log.warning("impossible d'enregistrer %s", path.name)


# --- surveillance -------------------------------------------------------------------


class SystemWatcher:
    def __init__(
        self,
        config: SystemConfig,
        on_problem: Callable[[Problem], None],
        on_resolved: Callable[[str], None],
        run: Callable[[list[str]], str] | None = None,
        fetch: Callable[[str], str] | None = None,
        memory: Path | None = None,
    ) -> None:
        self.config = config
        self.on_problem = on_problem
        self.on_resolved = on_resolved
        self.run = run or _run
        self.fetch = fetch or _fetch
        self._memory_path = memory
        self.memory = load_memory(memory)
        self._failed: dict[str, set[str]] = {"system": set(), "user": set()}
        self._low_disks: set[str] = set()
        self._log_offset: int | None = None
        self._log_size: int | None = None
        self._crashes_since = int(time.time() * 1_000_000)
        self._disk_checked = 0.0
        self._kernel_alert = False
        self._pacnew_alert = False
        self._pacman_changed = True  # au démarrage : les .pacnew déjà là
        self._news: list[News] = []
        self._news_fetched: float | None = None
        self._news_alerts: dict[str, datetime] = {}  # clé → date de publication
        self._upgrade_cache: tuple[int, datetime | None] = (-1, None)  # (taille du log, date)
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
            (self.config.updates or self.config.pacnew, self.check_updates),
            (self.config.crashes, self.check_crashes),
            (self.config.kernel, self.check_kernel),
            (self.config.news, self.check_news),
        ]
        if time.monotonic() - self._disk_checked >= DISK_INTERVAL:
            self._disk_checked = time.monotonic()
            checks.append((self.config.disk, self.check_disks))
            self._pacman_changed = True  # une fusion à la main ne passe pas par pacman.log
        if self._pacman_changed:
            checks.append((self.config.pacnew, self.check_pacnew))
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
        self._pacman_changed = True  # une mise à jour a pu laisser des .pacnew
        if not self.config.updates:
            return
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


    def _pacman_lines(self) -> list[str]:
        try:
            return PACMAN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

    def check_kernel(self, release: str | None = None) -> None:
        if PACMAN_LOCK.exists():
            return  # en pleine mise à jour, le dossier peut manquer un instant
        release = release or os.uname().release
        missing = kernel_missing(release, MODULES_DIR)
        if missing and not self._kernel_alert:
            self._kernel_alert = True
            installed = sorted(p.name for p in MODULES_DIR.iterdir() if p.is_dir())
            upgrades = kernel_upgrades(self._pacman_lines())[-3:]
            details = (
                f"Noyau en cours : {release}\n"
                f"Noyaux installés (/usr/lib/modules) : {', '.join(installed) or 'aucun'}\n"
                "Les modules du noyau en cours ont été supprimés par la mise à jour : un pilote pas "
                "encore chargé (clé USB, VPN, manette…) ne peut plus l'être avant le redémarrage."
                + ("\nDans /var/log/pacman.log :\n" + "\n".join(upgrades) if upgrades else "")
            )
            self._emit(Problem(KERNEL, "kernel", "noyau mis à jour : redémarre pour retrouver tes pilotes", details))
        elif not missing and self._kernel_alert:
            self._kernel_alert = False
            self.on_resolved("kernel")

    def check_pacnew(self, root: Path | None = None) -> None:
        """Signale les .pacnew pas encore signalés ; l'alerte s'éteint quand il n'y en a plus."""
        if PACMAN_LOCK.exists() or not PACMAN_LOG.exists():
            return
        self._pacman_changed = False
        found = find_pacnew(root or ETC)
        signaled = set(self.memory.get(PACNEW, []))
        new = {str(p) for p in found} - signaled
        if new:
            self._pacnew_alert = True
            plural = "s" if len(found) > 1 else ""
            details = "Fichiers .pacnew en attente (nouvelle version proposée par la mise à jour) :\n\n"
            details += "\n\n".join(f"{p}\n{pacnew_diff(p)}" for p in sorted(found))
            title = f"{len(found)} fichier{plural} .pacnew à fusionner dans /etc"
            self._emit(Problem(PACNEW, "pacnew", title, _clip(details)))
        elif not found and self._pacnew_alert:
            self._pacnew_alert = False
            self.on_resolved("pacnew")
        # Oublie ceux qui ont disparu : s'il en revient un après une mise à jour, il sera signalé.
        remembered = sorted(str(p) for p in found)
        if remembered != sorted(signaled):
            self.memory[PACNEW] = remembered
            save_memory(self.memory, self._memory_path)

    def _last_upgrade(self) -> datetime | None:
        try:
            size = PACMAN_LOG.stat().st_size
        except OSError:
            return None
        if self._upgrade_cache[0] != size:
            self._upgrade_cache = (size, last_full_upgrade(self._pacman_lines()))
        return self._upgrade_cache[1]

    def check_news(self) -> None:
        """News Arch publiées depuis ta dernière mise à jour complète : à lire avant la prochaine."""
        since = self._last_upgrade()
        if since is None:
            return  # pas Arch, ou jamais mis à jour : rien à comparer
        for key, published in list(self._news_alerts.items()):
            if published <= since:  # mise à jour faite depuis
                del self._news_alerts[key]
                self.on_resolved(key)
        now = time.monotonic()
        if self._news_fetched is not None and now - self._news_fetched < NEWS_INTERVAL:
            return
        try:
            self._news = parse_news(self.fetch(NEWS_URL))
            self._news_fetched = now
        except (OSError, ValueError, http.client.HTTPException) as exc:  # hors ligne, coupure… : plus tard
            log.info("news Arch injoignables (%s)", type(exc).__name__)
            self._news_fetched = now - NEWS_INTERVAL + NEWS_RETRY
            return
        signaled = set(self.memory.get(NEWS, []))
        pending = [n for n in self._news if n.published > since and n.guid not in signaled]
        for news in sorted(pending, key=lambda n: n.published):
            key = f"news:{news.guid}"
            self._news_alerts[key] = news.published
            details = (
                f"{news.title}\nPubliée le {news.published:%d/%m/%Y} : {news.link}\n"
                f"Ma dernière mise à jour complète : {since:%d/%m/%Y à %H:%M}\n\n{news.text}"
            )
            self._emit(Problem(NEWS, key, f"news Arch à lire avant de mettre à jour : {news.title}", _clip(details)))
        if pending:
            # Seulement celles encore dans le flux : le fichier ne grossit pas sans fin.
            current = {n.guid for n in self._news}
            self.memory[NEWS] = sorted((signaled & current) | {n.guid for n in pending})
            save_memory(self.memory, self._memory_path)


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
