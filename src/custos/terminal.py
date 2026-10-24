"""Ouvrir un terminal avec une commande : déjà tapée, que tu valides toi-même, ou lancée.

Préparée : `custos terminal` (Claude prépare une commande root) et la carte (clic droit sur
un bloc de commande, « Préparer »). Lancée : la carte (clic droit, « Exécuter »), avec le
shell de son bloc (```bash : bash, même si ton shell est fish) ; le terminal reste ouvert.
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


RUNNERS = {"bash", "sh", "zsh", "fish"}  # langues de bloc qui désignent un shell précis


def run_command(command: str, language: str, open_command: str) -> list[str]:
    """Terminal qui affiche `command`, la lance, puis laisse ton shell ouvert sur le résultat."""
    term = machine.terminal(open_command) or "foot"
    shell = machine.shell(term) or "bash"
    runner = language if language in RUNNERS else shell
    script = (
        f"printf '\\033[1m$ %s\\033[0m\\n' {shlex.quote(command)}; "
        f"{runner} -c {shlex.quote(command)}; exec {shell}"
    )
    return [term, "sh", "-c", script]


def _spawn(argv: list[str]) -> None:
    subprocess.Popen(
        argv, start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def run_with(command: str, language: str, open_command: str) -> None:
    """Lance la commande dans un terminal ; OSError si le terminal ne se lance pas."""
    _spawn(run_command(command, language, open_command))


def open_with(command: str, open_command: str) -> None:
    """Ouvre le terminal (celui de `open_command`, dans la config) avec la commande prête.

    Lève OSError si le terminal ne se lance pas.
    """
    argv = terminal_command(command, open_command)
    if argv[1] != "fish":
        clipboard.copy(command)
    _spawn(argv)
