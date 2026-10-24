"""Ouvrir un terminal avec une commande déjà tapée, que tu valides toi-même.

Utilisé par `custos terminal` (Claude prépare une commande root) et par la carte (clic
sur un bloc de commande).
"""

import shlex
import subprocess

from custos import machine
from custos.context import clipboard


def fish_quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def terminal_command(command: str, open_command: str) -> list[str]:
    """Terminal avec `command` déjà tapée sur la ligne de commande, sans l'exécuter."""
    term = machine.terminal(open_command) or "foot"
    shell = machine.shell(term) or "bash"
    if shell == "fish":
        return [term, "fish", "-C", f"commandline -- {fish_quote(command)}"]
    # Autres shells : pas de ligne pré-remplie, on l'affiche et on la copie.
    script = f"printf '%s\\n\\n' {shlex.quote(command)} '(copiée : colle-la pour la lancer)'; exec {shell}"
    return [term, "sh", "-c", script]


def open_with(command: str, open_command: str) -> None:
    """Ouvre le terminal (celui de `open_command`, dans la config) avec la commande prête.

    Lève OSError si le terminal ne se lance pas.
    """
    argv = terminal_command(command, open_command)
    if argv[1] != "fish":
        clipboard.copy(command)
    subprocess.Popen(
        argv, start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
