"""Ce que Custos sait de ta machine, détecté localement, pour des réponses qui s'y appliquent.

Seulement des faits génériques (distribution, versions, outils) : ni nom d'utilisateur,
ni chemins, ni contenu de fichiers.
"""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from custos.context import hyprland

AUR_HELPERS = ("yay", "paru")


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def distribution(os_release: Path = Path("/etc/os-release")) -> str:
    try:
        for line in os_release.read_text().splitlines():
            key, _, value = line.partition("=")
            if key == "PRETTY_NAME":
                return value.strip().strip('"')
    except OSError:
        pass
    return "Linux"


def terminal(open_command: str) -> str:
    """Le terminal de la commande « ouvrir en grand » de la config : celui que tu utilises."""
    try:
        words = shlex.split(open_command)
    except ValueError:
        return ""
    return Path(words[0]).name if words else ""


def shell(terminal_name: str) -> str:
    """Le shell interactif : celui que lance foot s'il est réglé, sinon le shell de connexion."""
    if terminal_name == "foot":
        try:
            for line in (_config_home() / "foot" / "foot.ini").read_text().splitlines():
                key, _, value = line.partition("=")
                if key.strip() == "shell" and value.strip():
                    return Path(value.split()[0]).name
        except OSError:
            pass
    return Path(os.environ.get("SHELL", "")).name


def version(program: str) -> str:
    """« fish, version 4.9.3 » → « 4.9.3 » (dernier mot de `programme --version`)."""
    try:
        result = subprocess.run(
            [program, "--version"], capture_output=True, text=True, timeout=2, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    words = result.stdout.split()
    return words[-1] if result.returncode == 0 and words else ""


def describe(open_command: str) -> str:
    """Quelques lignes sur la machine, pour le prompt système."""
    lines = [f"- Distribution : {distribution()}"]

    data = hyprland.request("version")
    hypr = data.get("version", "") if isinstance(data, dict) else ""
    lua = (_config_home() / "hypr" / "hyprland.lua").exists()
    if hypr or lua:
        lines.append(
            "- Compositeur : Hyprland"
            + (f" {hypr}" if hypr else "")
            + (", configuré en Lua (pas en hyprlang)" if lua else "")
        )
    if shutil.which("caelestia") or (_config_home() / "caelestia").is_dir():
        lines.append("- Shell graphique : caelestia (barre, lanceur, captures SUPER + SHIFT + S)")

    term = terminal(open_command)
    if term:
        lines.append(f"- Terminal : {term}")
    sh = shell(term)
    if sh:
        sh_version = version(sh)
        lines.append(f"- Shell : {sh}" + (f" {sh_version}" if sh_version else ""))

    helpers = [name for name in AUR_HELPERS if shutil.which(name)]
    if helpers:
        lines.append(f"- AUR : {', '.join(helpers)}")
    return "\n".join(lines)
