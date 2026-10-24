"""Historique des conversations de Custos, lu dans les fichiers de Claude Code.

Claude Code enregistre chaque session dans ~/.claude/projects/<dossier encodé>/<id>.jsonl ;
celles de Custos sont toutes dans le même dossier (voir claude.conversations_dir).
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from custos import claude

QUESTION_MARK = "Question : "  # fin du premier message (voir context.build_prompt)
WEEKDAYS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


@dataclass(frozen=True)
class Conversation:
    session: str
    updated: float  # date de la dernière modification (secondes depuis l'époque)
    title: str  # première question
    last_question: str
    last_answer: str
    exchanges: tuple[tuple[str, str], ...] = ()  # (question, réponse) dans l'ordre


def sessions_dir(workdir: Path | None = None) -> Path:
    """Dossier où Claude Code range les sessions lancées depuis `workdir`."""
    workdir = workdir or claude.conversations_dir()
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(workdir))


def _question(content) -> str | None:
    """Texte tapé par l'utilisateur dans un message, ou None (résultat d'outil, rien)."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        return None
    text = text.strip()
    # Premier message : le contexte (fenêtre, sélection…) puis « Question : … ».
    if QUESTION_MARK in text:
        text = text.rsplit(QUESTION_MARK, 1)[1].strip()
    return text or None


def _answer(content) -> str | None:
    if not isinstance(content, list):
        return None
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return text.strip() or None


def read(path: Path) -> Conversation | None:
    """Une conversation, ou None si elle n'a pas de réponse (interrompue, vide)."""
    questions: list[str] = []
    answers: list[str] = []  # la réponse à chaque question (vide si elle n'en a pas eu)
    try:
        with path.open(encoding="utf-8", errors="replace") as lines:
            for line in lines:
                if '"user"' not in line and '"assistant"' not in line:
                    continue  # la plupart des lignes : requêtes, pièces jointes…
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("isSidechain"):
                    continue  # sous-agents : pas la conversation principale
                content = (event.get("message") or {}).get("content")
                if event.get("type") == "user":
                    question = _question(content)
                    if question:
                        questions.append(question)
                        answers.append("")  # la réponse qui compte est celle d'après
                elif event.get("type") == "assistant" and answers:
                    text = _answer(content)
                    if text:
                        answers[-1] = text  # le dernier texte : la conclusion, après les outils
        updated = path.stat().st_mtime
    except OSError:
        return None
    if not questions or not answers[-1]:
        return None
    exchanges = tuple((q, a) for q, a in zip(questions, answers, strict=True) if a)
    return Conversation(path.stem, updated, questions[0], questions[-1], answers[-1], exchanges)


_cache: dict[Path, tuple[float, Conversation | None]] = {}


def conversations(workdir: Path | None = None) -> list[Conversation]:
    """Toutes les conversations, de la plus récente à la plus ancienne."""
    found = []
    for path in sessions_dir(workdir).glob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        cached = _cache.get(path)
        if cached is None or cached[0] != mtime:
            cached = _cache[path] = (mtime, read(path))
        if cached[1]:
            found.append(cached[1])
    return sorted(found, key=lambda c: c.updated, reverse=True)


def search(query: str, limit: int = 8, workdir: Path | None = None) -> list[Conversation]:
    """Les plus récentes dont la question ou la réponse contient tous les mots de `query`."""
    words = query.casefold().split()
    matches = [
        c
        for c in conversations(workdir)
        if all(w in f"{c.title}\n{c.last_question}\n{c.last_answer}".casefold() for w in words)
    ]
    return matches[:limit]


def find(session: str, workdir: Path | None = None) -> Conversation | None:
    return next((c for c in conversations(workdir) if c.session == session), None)


def when(timestamp: float, now: float | None = None) -> str:
    """« 09:10 », « hier 23:33 », « jeu. 19:54 », « 12/10 »."""
    day = datetime.fromtimestamp(timestamp)
    today = datetime.fromtimestamp(now if now is not None else time.time()).date()
    hour = day.strftime("%H:%M")
    if day.date() == today:
        return hour
    if day.date() == today - timedelta(days=1):
        return f"hier {hour}"
    if today - day.date() < timedelta(days=7):
        return f"{WEEKDAYS[day.weekday()]} {hour}"
    return day.strftime("%d/%m")
