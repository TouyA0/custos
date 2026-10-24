"""Appels à Claude via `claude -p` (abonnement Claude, pas de clé API).

Claude n'est appelé que sur une action de l'utilisateur ; les réactions
automatiques de la mascotte restent locales.
"""

import base64
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from custos.config import ClaudeConfig
from custos.supervisor import state_dir

SYSTEM_PROMPT = """\
Tu es Custos, une petite mascotte gardienne qui vit sur le bureau Linux de \
l'utilisateur. Tu réponds dans une petite bulle : sois bref, clair et direct, en \
français sauf si on te parle dans une autre langue. Pas de longues introductions. \
Quand tu proposes une commande, mets-la dans un bloc de code, adaptée à sa machine."""

# Avec les outils (mode agent) : Custos agit sur la machine, la bulle ne montre que la fin.
AGENT_PROMPT = """\
Tu as les outils de Claude Code et tu agis directement sur sa machine pour faire ce \
qu'il demande : lire des fichiers et des journaux, lancer des commandes, modifier sa \
configuration, ouvrir une page (`xdg-open URL`) ou une application. Fais-le toi-même \
plutôt que de lui expliquer comment faire.
- Ta réponse finale est la seule chose affichée dans la bulle : donne seulement la \
conclusion (ce que tu as trouvé ou fait, si ça a marché, ce qui lui reste à faire), \
pas le récit de tes étapes. Le détail reste consultable en ouvrant la conversation en grand.
- Tu n'as pas de mot de passe : n'utilise jamais sudo. Pour une commande qui doit \
tourner en root, lance `{custos} terminal 'la commande'` : un terminal s'ouvre avec la \
commande prête, c'est lui qui la valide et tape son mot de passe. Dis-le dans ta conclusion.
- Pour un rappel ou un minuteur (« rappelle-moi dans 20 min de… », « préviens-moi à 15 h »), \
lance `{custos} remind QUAND TEXTE`, où QUAND vaut 20m, 1h30, 15:30, demain 9h ou une date \
ISO comme 2026-10-25T09:00 (lance `date` si tu dois la calculer). La mascotte le lui \
rappellera à l'heure dite, même une fois la conversation finie. `{custos} remind --list` \
liste ceux en attente, `{custos} remind --cancel ID` en annule un.
- Ne lis jamais ses secrets (clés SSH, GPG, gestionnaire de mots de passe, jetons)."""

# Toujours en dernier : le caractère choisi change le ton, pas la longueur.
BREVITY = "Quel que soit ton caractère, reste bref : tes réponses s'affichent dans une petite bulle."

# Mode agent : jamais les secrets. Chemins depuis ton dossier personnel : dossiers ou fichiers.
SECRET_PATHS = [
    ".ssh", ".gnupg", ".local/share/keyrings", ".password-store",
    ".claude/.credentials.json", ".config/gh/hosts.yml", ".git-credentials", ".netrc",
    ".aws", ".docker/config.json", ".kube",
    ".mozilla", ".config/BraveSoftware", ".config/google-chrome", ".config/chromium",
]

# Jamais root (pas de mot de passe : `custos terminal` le lui laisse), et l'outil Read ne lit
# pas les secrets. Bash, lui, est arrêté par le bac à sable (voir sandbox_settings) : une règle
# sur le texte de la commande se contourne trop facilement (`cat ~/.s*/id_ed25519`).
DISALLOWED_TOOLS = [
    *(f"Bash({escalate} *)" for escalate in ("sudo", "doas", "pkexec", "run0", "su")),
    "Bash(su)",
    *(rule for path in SECRET_PATHS for rule in (f"Read(~/{path})", f"Read(~/{path}/**)")),
    "Read(**/*.kdbx)",
    "Read(**/.env)",
]


def sandbox_settings() -> dict:
    """Bac à sable de Claude Code pour ses commandes (bubblewrap) : les secrets y sont
    invisibles, quelle que soit la commande ; tout ton dossier reste modifiable, le réseau
    et les sockets (Hyprland, D-Bus) restent ouverts. `custos` et `xdg-open` tournent en
    dehors : le terminal pour sudo, le navigateur. Une commande qui a besoin d'en sortir
    passe par une demande de permission dans la carte."""
    home = Path.home()
    return {
        "sandbox": {
            "enabled": True,
            "autoAllowBashIfSandboxed": False,  # le mode auto juge toujours chaque commande
            "excludedCommands": [f"{custos_executable()} *", "custos *", "xdg-open *"],
            "network": {"allowedDomains": ["*"], "allowAllUnixSockets": True},
            "filesystem": {
                "allowWrite": [str(home)],
                "denyRead": [str(home / path) for path in SECRET_PATHS],
            },
        }
    }


def sandbox_available() -> bool:
    """bubblewrap et socat : ce qu'il faut au bac à sable de Claude Code sous Linux."""
    return bool(shutil.which("bwrap") and shutil.which("socat"))


ALLOW, ALWAYS, DENY = "allow", "always", "deny"  # réponses à une demande de permission


@dataclass
class Permission:
    """Demande de Claude pour une action que le mode auto juge sensible."""

    tool: str
    input: dict

    @property
    def description(self) -> str:
        return describe_permission(self.tool, self.input)


def _home(text: str) -> str:
    home = str(Path.home())
    return text.replace(home, "~") if home != "/" else text


def describe_tool(name: str, data: dict) -> str:
    """Étape en cours, en une ligne : « $ journalctl -b -1 », « lit ~/.config/… »."""
    if name == "Bash":
        command = (data.get("command") or "").strip().splitlines()
        return f"$ {_home(command[0])}" if command else "$ commande"
    if name == "Read":
        return f"lit {_home(data.get('file_path', ''))}"
    if name in ("Edit", "Write", "NotebookEdit"):
        return f"modifie {_home(data.get('file_path') or data.get('notebook_path') or '')}"
    if name in ("Grep", "Glob"):
        return f"cherche « {data.get('pattern', '')} »"
    if name == "WebSearch":
        return f"cherche sur le web : {data.get('query', '')}"
    if name == "WebFetch":
        return f"lit {data.get('url', '')}"
    if name in ("Task", "Agent"):
        return f"délègue : {data.get('description', '')}"
    return name


def describe_permission(name: str, data: dict) -> str:
    """Ce que Claude veut faire, en entier : la commande exacte, le fichier, l'adresse."""
    if name == "Bash":
        return (data.get("command") or "").strip()
    if name in ("Edit", "Write", "NotebookEdit"):
        return f"Modifier {_home(data.get('file_path') or data.get('notebook_path') or '')}"
    if name == "WebFetch":
        return f"Ouvrir {data.get('url', '')}"
    return f"{name} {json.dumps(data, ensure_ascii=False)[:300]}"


def custos_executable() -> str:
    """Chemin de la commande `custos` : ~/.local/bin n'est pas forcément dans le PATH du daemon."""
    beside = Path(sys.executable).with_name("custos")
    return str(beside) if beside.exists() else (shutil.which("custos") or "custos")


def build_system_prompt(machine: str = "", persona: str = "", agent: bool = False) -> str:
    """Prompt système : la base, le mode agent, la machine détectée, puis ce que
    l'utilisateur a écrit dans custos.md (caractère, préférences)."""
    parts = [SYSTEM_PROMPT]
    if agent:
        parts.append(AGENT_PROMPT.format(custos=custos_executable()))
    if machine:
        parts.append(f"Sa machine (détectée automatiquement) :\n{machine}")
    if persona:
        parts.append(f"Ce que l'utilisateur t'a écrit (ton caractère, lui, ses préférences) :\n{persona}")
    parts.append(BREVITY)
    return "\n\n".join(parts)

MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


MAX_ERROR = 2_000  # caractères : la fin de la sortie d'erreur, affichée dans la carte


class ClaudeError(Exception):
    pass


def conversations_dir() -> Path:
    """Dossier de travail des conversations de Custos.

    Claude Code range ses sessions par dossier : elles sont ainsi toutes ensemble,
    à part de tes projets, et `claude --resume` lancé ici ne liste qu'elles.
    """
    return state_dir() / "conversations"


def build_command(
    config: ClaudeConfig,
    system_prompt: str = SYSTEM_PROMPT,
    resume: str | None = None,
    agent: bool = False,
    model: str | None = None,
) -> list[str]:
    # Ni réglages, ni hooks, ni serveurs MCP de l'utilisateur. La session est enregistrée
    # pour les questions de suivi (--resume) et pour l'ouvrir en grand dans un terminal.
    command = [
        "claude", "-p",
        "--model", model or config.model,
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--setting-sources", "",
        "--strict-mcp-config",
    ]
    if agent:
        # Les outils de Claude Code en mode auto : il agit seul, et ce que le mode auto
        # juge sensible nous est demandé (control_request « can_use_tool », voir ask).
        command += [
            "--permission-mode", "auto",
            "--permission-prompts", "host",
            "--permission-prompt-tool", "stdio",
            "--add-dir", str(Path.home()),
            "--disallowedTools", *DISALLOWED_TOOLS,
        ]
        if config.sandbox and sandbox_available():
            command += ["--settings", json.dumps(sandbox_settings())]
    else:
        command += ["--tools", ""]  # rien que répondre
    command += ["--system-prompt", system_prompt]
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


def parse_tools(line: str) -> list[str]:
    """Étapes annoncées dans une ligne : les outils qu'un message de Claude utilise."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return []
    if event.get("type") != "assistant":
        return []
    return [
        describe_tool(block.get("name", ""), block.get("input") or {})
        for block in event.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def parse_permission(line: str) -> tuple[str, Permission] | None:
    """Demande de permission (control_request « can_use_tool ») : son identifiant et son objet."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    request = event.get("request") or {}
    if event.get("type") != "control_request" or request.get("subtype") != "can_use_tool":
        return None
    permission = Permission(request.get("tool_name", ""), request.get("input") or {})
    return event.get("request_id", ""), permission


def permission_response(request_id: str, permission: Permission, choice: str) -> dict:
    """Réponse à envoyer à Claude Code pour une demande de permission."""
    # « Toujours » est retenu par Custos (voir daemon) : pour Claude Code, c'est « autoriser ».
    if choice == DENY:
        decision = {"behavior": "deny", "message": "L'utilisateur a refusé cette action."}
    else:
        decision = {"behavior": "allow", "updatedInput": permission.input}
    return {
        "type": "control_response",
        "response": {"subtype": "success", "request_id": request_id, "response": decision},
    }


def parse_rate_limit(line: str) -> dict | None:
    """Quota annoncé par Claude Code (`rate_limit_event`) : usage des fenêtres de 5 h, 7 j…"""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(event, dict) or event.get("type") != "rate_limit_event":
        return None
    info = event.get("rate_limit_info")
    return info if isinstance(info, dict) else None


def _is_result(line: str) -> bool:
    try:
        return json.loads(line).get("type") == "result"
    except (json.JSONDecodeError, AttributeError):
        return False


def ask(
    prompt: str,
    config: ClaudeConfig,
    images: list[Path] | None = None,
    system_prompt: str = SYSTEM_PROMPT,
    resume: str | None = None,
    on_session: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    agent: bool = False,
    on_tool: Callable[[str], None] | None = None,
    on_permission: Callable[[Permission], str] | None = None,
    model: str | None = None,
    on_rate_limit: Callable[[dict], None] | None = None,
) -> Iterator[str]:
    """Envoie `prompt` à Claude et renvoie la réponse morceau par morceau.

    `resume` : session à poursuivre (question de suivi). `on_session` reçoit
    l'identifiant de la session, pour la poursuivre ou l'ouvrir en grand ensuite.
    `cancel` : une fois levé, `claude` est arrêté et la réponse s'interrompt sans erreur.
    `agent` : avec les outils de Claude Code. `on_tool` reçoit chaque étape (le texte déjà
    reçu n'était alors qu'un commentaire, pas la réponse) ; `on_permission` décide d'une
    action sensible (ALLOW, ALWAYS ou DENY) et peut attendre ta réponse.
    `model` : un autre modèle que celui de la config, pour cette question (`/vite`).
    """
    message = build_message(prompt, images)
    workdir = conversations_dir()
    workdir.mkdir(parents=True, exist_ok=True)
    # Sortie d'erreur dans un fichier, lu seulement en cas d'échec : un tube que personne ne
    # lit pendant la réponse se remplirait (--verbose) et bloquerait `claude`.
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as errors:
        try:
            process = subprocess.Popen(
                build_command(config, system_prompt, resume, agent, model),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
                # Dossier à part : pas de CLAUDE.md de projet chargé par erreur.
                cwd=workdir,
            )
        except FileNotFoundError as exc:
            raise ClaudeError("la commande `claude` est introuvable") from exc

        timeout = config.agent_timeout if agent else config.timeout
        timer = threading.Timer(timeout, process.kill)
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
            # L'entrée reste ouverte jusqu'au résultat : on y répond aux demandes de permission.
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            for line in process.stdout:
                rate_limit = parse_rate_limit(line)
                if rate_limit and on_rate_limit:
                    on_rate_limit(rate_limit)
                session = parse_session(line)
                if session and on_session:
                    on_session(session)
                for step in parse_tools(line):
                    if on_tool:
                        on_tool(step)
                request = parse_permission(line)
                if request:
                    request_id, permission = request
                    choice = on_permission(permission) if on_permission else DENY
                    process.stdin.write(json.dumps(permission_response(request_id, permission, choice)) + "\n")
                    process.stdin.flush()
                text = parse_line(line)
                if text:
                    received = True
                    yield text
                if _is_result(line):
                    process.stdin.close()  # fin de la conversation : `claude` peut s'arrêter
            process.wait()
        except (BrokenPipeError, ValueError):
            process.wait()  # `claude` arrêté (fermeture, délai dépassé) pendant qu'on lui écrivait
        finally:
            timer.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()

        if cancel and cancel.is_set():
            return  # interrompue à ta demande : pas une erreur
        if process.returncode == -9:
            raise ClaudeError(f"pas de réponse après {timeout} s")
        if process.returncode != 0 and not received:
            errors.seek(0)
            stderr = errors.read()[-MAX_ERROR:].strip()
            raise ClaudeError(stderr or f"`claude` a quitté avec le code {process.returncode}")
