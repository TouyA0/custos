"""Quota de l'abonnement Claude, tel que Claude Code l'annonce à chaque question.

Le flux de `claude -p` contient un `rate_limit_event` : l'usage de chaque fenêtre (5 h,
7 jours…) et quand elle se remet à zéro. Custos le retient (et le garde d'un démarrage à
l'autre) pour l'afficher en haut du menu de l'icône de la barre : aucun appel en plus, rien
de secret. (caelestia n'affiche pas les info-bulles des icônes, d'où le menu.)
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from custos.supervisor import state_dir

WINDOW_NAMES = {
    "five_hour": "5 h",
    "seven_day": "7 j",
    "seven_day_opus": "7 j Opus",
    "seven_day_sonnet": "7 j Sonnet",
}
WEEKDAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
SAVE_FAST = 0.8  # à partir de 80 % sur 5 h, l'info-bulle suggère /vite


@dataclass
class Window:
    name: str  # five_hour, seven_day…
    utilization: float  # 0 à 1
    resets_at: float  # secondes depuis l'époque


@dataclass
class Quota:
    windows: list[Window] = field(default_factory=list)
    status: str = "allowed"  # allowed, allowed_warning ou rejected
    at: float = 0.0  # quand Claude Code l'a annoncé

    @classmethod
    def from_event(cls, info: dict, now: float | None = None) -> "Quota | None":
        """À partir de `rate_limit_info` ; None s'il ne contient pas d'usage."""
        windows = []
        for name, window in (info.get("unifiedWindows") or {}).items():
            if not isinstance(window, dict) or "utilization" not in window:
                continue
            try:
                windows.append(Window(name, float(window["utilization"]), float(window.get("resetsAt") or 0)))
            except (TypeError, ValueError):
                continue
        if not windows:
            return None
        order = list(WINDOW_NAMES)
        windows.sort(key=lambda w: order.index(w.name) if w.name in order else len(order))
        return cls(windows, str(info.get("status") or "allowed"), now if now is not None else time.time())

    def lines(self, now: float | None = None) -> list[str]:
        """Une ligne par fenêtre, pour le menu : « Quota 5 h : 63 % · jusqu'à 17:30 »,
        puis un conseil près de la limite."""
        now = now if now is not None else time.time()
        lines = []
        for window in self.windows:
            label = f"Quota {WINDOW_NAMES.get(window.name, window.name)}"
            if window.resets_at and window.resets_at <= now:
                lines.append(f"{label} : remis à zéro")  # la fenêtre a recommencé depuis
                continue
            percent = round(window.utilization * 100)
            until = f" · jusqu'à {when(window.resets_at, now)}" if window.resets_at else ""
            lines.append(f"{label} : {percent} %{until}")
        five_hours = next((w for w in self.windows if w.name == "five_hour"), None)
        if self.status == "rejected":
            lines.insert(0, "Quota atteint")
        elif lines and five_hours and five_hours.resets_at > now and five_hours.utilization >= SAVE_FAST:
            lines.append("Presque au bout : /vite pour l'économiser")
        return lines

    def describe(self, now: float | None = None) -> str:
        """Les mêmes lignes en une seule : « Quota 5 h : 63 % · jusqu'à 17:30 ; … »."""
        return " ; ".join(self.lines(now))


def when(timestamp: float, now: float) -> str:
    """Heure à venir : « 17:30 », « demain 09:00 », « jeu. 09:00 »."""
    moment = datetime.fromtimestamp(timestamp)
    today = datetime.fromtimestamp(now).date()
    hour = moment.strftime("%H:%M")
    if moment.date() == today:
        return hour
    if moment.date() == today + timedelta(days=1):
        return f"demain {hour}"
    return f"{WEEKDAYS[moment.weekday()]} {hour}"


def path() -> Path:
    return state_dir() / "quota.json"


def save(quota: Quota, file: Path | None = None) -> None:
    file = file or path()
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(asdict(quota)))
    except OSError:
        pass  # sans importance : il sera relu à la prochaine question


def load(file: Path | None = None) -> Quota | None:
    try:
        data = json.loads((file or path()).read_text())
        return Quota([Window(**w) for w in data["windows"]], data.get("status", "allowed"), data.get("at", 0.0))
    except (OSError, ValueError, KeyError, TypeError):
        return None
