"""Rappels et minuteurs : « rappelle-moi dans 20 min de sortir le linge ».

Locaux, sans Claude : `custos remind 20m sortir le linge` (que Claude lance lui-même quand
tu le lui demandes), ou `/rappel 20m …` dans la carte. À l'heure dite, la mascotte se
réveille avec un badge, et une notification s'affiche. Gardés dans
~/.local/state/custos/reminders.json : un rappel survit à un redémarrage, et celui dont
l'heure est passée pendant que Custos était arrêté sonne à son retour.

Quand : une durée (`20m`, `20min`, `1h30`, `dans 2h`, `90s`, ou un nombre seul : des minutes),
une heure (`15:30` ou `à 15h30`, demain si elle est passée), `demain 9h`, ou une date ISO
(`2026-10-25T09:00`). Sans « à », `1h30` est une durée : une heure s'écrit avec « : ».
"""

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from custos.supervisor import state_dir

log = logging.getLogger(__name__)

MAX_TEXT = 300  # caractères
MAX_DELAY = timedelta(days=366)

# `2h`, `1h30`, `20min`, `90s`, `1h 5m 30s`… (un nombre seul : des minutes, voir parse_when).
# Possessifs (\d++) : « 30 » n'est jamais coupé en « 3 » et « 0 ».
DURATION = re.compile(
    r"(?:(?P<h>\d++)\s*h(?:eures?)?\s*(?:(?P<hm>\d++)\s*(?:minutes?|min|mn|m)?)?)?\s*"
    r"(?:(?P<m>\d++)\s*(?:minutes?|min|mn|m))?\s*"
    r"(?:(?P<s>\d++)\s*(?:secondes?|sec|s))?"
)
CLOCK = re.compile(r"(?P<h>\d{1,2})(?::(?P<m>\d{2})|h(?P<hm>\d{2})?)")
USAGE = "Quand : 20m, 1h30, 15:30, à 15h30, demain 9h ou 2026-10-25T09:00."
PREFIXES = {"dans", "à", "a", "demain"}  # suivis de la durée ou de l'heure


class WhenError(ValueError):
    pass


@dataclass(frozen=True)
class Reminder:
    id: str
    at: float  # heure de sonnerie (secondes depuis l'époque)
    text: str


def _clock(text: str, day: datetime, h_ok: bool = True) -> datetime | None:
    """`15:30` (ou `15h30`, `15h` si `h_ok`) ce jour-là ; None si ce n'est pas une heure."""
    match = CLOCK.fullmatch(text)
    if not match or (match["m"] is None and not h_ok):
        return None
    hour, minute = int(match["h"]), int(match["m"] or match["hm"] or 0)
    if hour > 23 or minute > 59:
        raise WhenError(f"« {text} » n'est pas une heure.")
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_when(text: str, now: datetime) -> datetime:
    """Heure de sonnerie ; WhenError (message lisible) si `text` n'en est pas une."""
    text = " ".join(text.split()).casefold()
    if not text:
        raise WhenError(USAGE)
    first, _, rest = text.partition(" ")
    if first == "demain":
        rest = rest.removeprefix("à ").removeprefix("a ")
        at = _clock(rest, now + timedelta(days=1))
        if at is None:
            raise WhenError(f"« {text} » : écris l'heure comme 9h ou 9:00. {USAGE}")
    elif first in ("à", "a") or (at := _clock(text, now, h_ok=False)) is not None:
        at = _clock(rest, now) if first in ("à", "a") else at
        if at is None:
            raise WhenError(f"« {text} » : écris l'heure comme 15h30 ou 15:30. {USAGE}")
        if at <= now:
            at += timedelta(days=1)  # 8:00 alors qu'il est 15 h : demain matin
    elif (duration := text.removeprefix("dans ")).isdigit():
        at = now + timedelta(minutes=int(duration))
    elif (match := DURATION.fullmatch(duration)) and any(match.groupdict().values()):
        minutes = int(match["hm"] or 0) + int(match["m"] or 0)
        at = now + timedelta(hours=int(match["h"] or 0), minutes=minutes, seconds=int(match["s"] or 0))
    else:
        try:
            at = datetime.fromisoformat(text.upper())
        except ValueError:
            raise WhenError(f"Je ne comprends pas « {text} ». {USAGE}") from None
        if at.tzinfo is not None:
            at = at.astimezone().replace(tzinfo=None)
    if at <= now:
        raise WhenError("Cette heure est déjà passée.")
    if at - now > MAX_DELAY:
        raise WhenError("C'est dans plus d'un an : trop loin pour moi.")
    return at


def split(words: str) -> tuple[str, str]:
    """`demain à 9h appeler maman` → (« demain à 9h », « appeler maman »)."""
    parts = words.split()
    count = 0
    while count < len(parts) - 1 and parts[count].casefold() in PREFIXES:
        count += 1
    return " ".join(parts[: count + 1]), " ".join(parts[count + 1 :])


def create(when: str, text: str, now: datetime | None = None) -> Reminder:
    """Un nouveau rappel ; WhenError si l'heure ou le texte ne vont pas."""
    at = parse_when(when, now or datetime.now())  # d'abord l'heure : « /rappel bof » parle de « bof »
    text = " ".join(text.split())
    if not text:
        raise WhenError("De quoi faut-il te rappeler ?")
    return Reminder(uuid.uuid4().hex[:6], at.timestamp(), text[:MAX_TEXT])


def describe(at: float, now: datetime | None = None) -> str:
    """« à 15:20 (dans 20 min) », « demain à 09:00 », « le 25/10 à 09:00 »."""
    now = now or datetime.now()
    when = datetime.fromtimestamp(at)
    if when.date() == now.date():
        minutes = max(round((when - now).total_seconds() / 60), 0)
        if minutes < 60:
            delay = f"dans {minutes} min" if minutes else "dans moins d'une minute"
        else:
            hours, rest = divmod(minutes, 60)
            delay = f"dans {hours} h {rest:02d}" if rest else f"dans {hours} h"
        return f"à {when:%H:%M} ({delay})"
    if when.date() == (now + timedelta(days=1)).date():
        return f"demain à {when:%H:%M}"
    return f"le {when:%d/%m} à {when:%H:%M}"


def store_path() -> Path:
    return state_dir() / "reminders.json"


def load(path: Path | None = None) -> list[Reminder]:
    """Rappels en attente, du plus proche au plus lointain ; une entrée abîmée est sautée."""
    try:
        data = json.loads((path or store_path()).read_text())
    except (OSError, ValueError):
        return []
    reminders = []
    for item in data if isinstance(data, list) else []:
        try:
            reminders.append(Reminder(str(item["id"]), float(item["at"]), str(item["text"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(reminders, key=lambda r: r.at)


def save(reminders: list[Reminder], path: Path | None = None) -> None:
    path = path or store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(r) for r in reminders], ensure_ascii=False, indent=1))
        tmp.replace(path)
    except OSError:
        log.warning("impossible d'enregistrer les rappels")
