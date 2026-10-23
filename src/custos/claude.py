"""Appels à Claude via `claude -p` (abonnement Claude, pas de clé API).

Claude n'est appelé que sur une action de l'utilisateur ; les réactions
automatiques de la mascotte restent locales.
"""

import base64
import json
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

from custos.config import ClaudeConfig

SYSTEM_PROMPT = """\
Tu es Custos, une petite mascotte gardienne qui vit sur le bureau Linux \
(Arch, Hyprland) de l'utilisateur. Tu réponds dans une petite bulle : sois bref, \
clair et direct, en français sauf si on te parle dans une autre langue. \
Pas de longues introductions. Quand tu proposes une commande, mets-la dans un \
bloc de code."""

MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class ClaudeError(Exception):
    pass


def build_command(config: ClaudeConfig, system_prompt: str = SYSTEM_PROMPT) -> list[str]:
    # Aucun outil, aucune session enregistrée, aucun réglage ni serveur MCP :
    # Custos n'utilise Claude que pour répondre, jamais pour agir.
    return [
        "claude", "-p",
        "--model", config.model,
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--tools", "",
        "--no-session-persistence",
        "--setting-sources", "",
        "--strict-mcp-config",
        "--system-prompt", system_prompt,
    ]


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


def ask(
    prompt: str,
    config: ClaudeConfig,
    images: list[Path] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
) -> Iterator[str]:
    """Envoie `prompt` à Claude et renvoie la réponse morceau par morceau."""
    message = build_message(prompt, images)
    try:
        process = subprocess.Popen(
            build_command(config, system_prompt),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # Dossier neutre : pas de CLAUDE.md de projet chargé par erreur.
            cwd=tempfile.gettempdir(),
        )
    except FileNotFoundError as exc:
        raise ClaudeError("la commande `claude` est introuvable") from exc

    timer = threading.Timer(config.timeout, process.kill)
    timer.start()
    received = False
    try:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.close()
        for line in process.stdout:
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

    if process.returncode == -9:
        raise ClaudeError(f"pas de réponse après {config.timeout} s")
    if process.returncode != 0 and not received:
        stderr = process.stderr.read().strip()
        raise ClaudeError(stderr or f"`claude` a quitté avec le code {process.returncode}")
