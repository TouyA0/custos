"""Appels à Claude via `claude -p` (abonnement Claude, pas de clé API).

Claude n'est appelé que sur une action de l'utilisateur ; les réactions
automatiques de la mascotte restent locales.
"""

import base64
import json
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from custos.config import ClaudeConfig
from custos.supervisor import state_dir

SYSTEM_PROMPT = """\
Tu es Custos, une petite mascotte gardienne qui vit sur le bureau Linux \
(Arch, Hyprland) de l'utilisateur. Tu réponds dans une petite bulle : sois bref, \
clair et direct, en français sauf si on te parle dans une autre langue. \
Pas de longues introductions. Quand tu proposes une commande, mets-la dans un \
bloc de code."""

MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class ClaudeError(Exception):
    pass


def conversations_dir() -> Path:
    """Dossier de travail des conversations de Custos.

    Claude Code range ses sessions par dossier : elles sont ainsi toutes ensemble,
    à part de tes projets, et `claude --resume` lancé ici ne liste qu'elles.
    """
    return state_dir() / "conversations"


def build_command(
    config: ClaudeConfig, system_prompt: str = SYSTEM_PROMPT, resume: str | None = None
) -> list[str]:
    # Aucun outil, aucun réglage ni serveur MCP : dans la bulle, Custos n'utilise
    # Claude que pour répondre, jamais pour agir. La session est enregistrée pour les
    # questions de suivi (--resume) et pour l'ouvrir en grand dans un terminal.
    command = [
        "claude", "-p",
        "--model", config.model,
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--tools", "",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--system-prompt", system_prompt,
    ]
    if resume:
        command += ["--resume", resume]
    return command


def build_message(prompt: str, images: list[Path] | None = None) -> dict:
    content = []
    for image in images or []:
        media_type = MEDIA_TYPES.get(image.suffix.lower())
        if media_type is None:
            raise ClaudeError(f"format d'image non pris en charge : {image.name}")
        data = base64.b64encode(image.read_bytes()).decode()
        content.append(
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
        )
    content.append({"type": "text", "text": prompt})
    return {"type": "user", "message": {"role": "user", "content": content}}


def parse_line(line: str) -> str | None:
    """Renvoie le morceau de texte contenu dans une ligne de sortie, s'il y en a un.

    Lève ClaudeError si la ligne est un résultat en erreur.
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None

    if event.get("type") == "stream_event":
        delta = event.get("event", {}).get("delta", {})
        if delta.get("type") == "text_delta":
            return delta.get("text")
    elif event.get("type") == "result" and event.get("is_error"):
        raise ClaudeError(event.get("result") or "erreur inconnue")
    return None


def parse_session(line: str) -> str | None:
    """Identifiant de session annoncé au début de la sortie de `claude -p`."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if event.get("type") == "system" and event.get("subtype") == "init":
        return event.get("session_id")
    return None


def ask(
    prompt: str,
    config: ClaudeConfig,
    images: list[Path] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    resume: str | None = None,
    on_session: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    """Envoie `prompt` à Claude et renvoie la réponse morceau par morceau.

    `resume` : session à poursuivre (question de suivi). `on_session` reçoit
    l'identifiant de la session, pour la poursuivre ou l'ouvrir en grand ensuite.
    `cancel` : une fois levé, `claude` est arrêté et la réponse s'interrompt sans erreur.
    """
    message = build_message(prompt, images)
    workdir = conversations_dir()
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        process = subprocess.Popen(
            build_command(config, system_prompt, resume),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Dossier à part : pas de CLAUDE.md de projet chargé par erreur.
            cwd=workdir,
        )
    except FileNotFoundError as exc:
        raise ClaudeError("la commande `claude` est introuvable") from exc

    timer = threading.Timer(config.timeout, process.kill)
    timer.start()
    if cancel:
        # Arrêt immédiat, même si Claude n'a encore rien envoyé.
        def watch_cancel() -> None:
            while process.poll() is None:
                if cancel.wait(0.1):
                    process.kill()
                    return

        threading.Thread(target=watch_cancel, daemon=True).start()
    received = False
    try:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.close()
        for line in process.stdout:
            session = parse_session(line)
            if session and on_session:
                on_session(session)
            text = parse_line(line)
            if text:
                received = True
                yield text
        process.wait()
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
            process.wait()

    if cancel and cancel.is_set():
        return  # interrompue à ta demande : pas une erreur
    if process.returncode == -9:
        raise ClaudeError(f"pas de réponse après {config.timeout} s")
    if process.returncode != 0 and not received:
        stderr = process.stderr.read().strip()
        raise ClaudeError(stderr or f"`claude` a quitté avec le code {process.returncode}")
